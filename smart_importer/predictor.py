"""Machine learning importer decorators."""

import logging
import operator
from typing import List, Union

from sklearn.pipeline import make_pipeline, FeatureUnion
from sklearn.svm import SVC

from beancount.core.data import Transaction, ALL_DIRECTIVES, filter_txns

from smart_importer.entries import merge_non_transaction_entries
from smart_importer.entries import set_entry_attribute
from smart_importer.pipelines import get_pipeline
from smart_importer.decorator import ImporterDecorator

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class SmartImporterDecorator(ImporterDecorator):
    """Base class for machine learning importer helpers.

    Args:
        predict: Whether to add predictions to the entries.
        suggest: Whether to add a list of suggestions as metadata to the
            entries.
        overwrite: When an attribute is predicted but already exists on an
            entry, overwrite the existing one.
    """

    weights = {}
    attribute = None

    def __init__(self, predict=True, suggest=False, overwrite=False):
        super().__init__()
        self.training_data = None
        self.pipeline = None

        self.predict = predict
        self.suggest = suggest
        self.overwrite = overwrite

    def main(self, imported_entries, existing_entries):
        """Predict and suggest attributes for imported transactions."""
        try:
            self.load_training_data(existing_entries)
            self.prepare_training_data()
            self.define_pipeline()
            self.train_pipeline()
            return self.process_entries(imported_entries)
        except (ValueError, AssertionError) as exception:
            logger.error(exception)
            return imported_entries

    def load_training_data(self, existing_entries):
        """Load training data, i.e., a list of Beancount entries."""
        training_data = existing_entries or []
        training_data = list(filter_txns(training_data))
        if self.account:
            training_data = [
                txn for txn in training_data
                if any([pos.account == self.account for pos in txn.postings])
            ]
            logger.debug(
                f"After filtering for account {self.account}, "
                f"the training data consists of {len(training_data)} entries.")
        self.training_data = training_data

    @property
    def targets(self):
        """The training targets for the given training data.

        Returns:
            A list training targets (of the same length as the training data).
        """
        if not self.attribute:
            raise NotImplementedError
        return [
            getattr(entry, self.attribute) or ''
            for entry in self.training_data
        ]

    def prepare_training_data(self):
        """Modify the training data if necessary."""
        pass

    def define_pipeline(self):
        """Defines the machine learning pipeline based on given weights."""

        transformers = []
        for attribute in self.weights:
            transformers.append((attribute, get_pipeline(attribute)))

        self.pipeline = make_pipeline(
            FeatureUnion(
                transformer_list=transformers,
                transformer_weights=self.weights),
            SVC(kernel='linear'),
        )

    def train_pipeline(self):
        """Train the machine learning pipeline."""

        if not self.training_data:
            logger.error("Cannot train the machine learning model "
                         "because the training data is empty.")
            return

        self.pipeline.fit(self.training_data, self.targets)
        logger.debug("Trained the machine learning model.")

    def process_entries(self, imported_entries) -> List[Union[ALL_DIRECTIVES]]:
        """Process imported entries.

        Transactions might be modified, all other entries are left as is.

        :return: Returns the list of entries to be imported.
        """
        imported_transactions: List[Transaction]
        imported_transactions = list(filter_txns(imported_entries))
        enhanced_transactions = self.process_transactions(
            list(imported_transactions))
        return merge_non_transaction_entries(imported_entries,
                                             enhanced_transactions)

    def apply_prediction(self, entry, prediction):
        """Apply a single prediction to an entry."""
        if not self.attribute:
            raise NotImplementedError
        return set_entry_attribute(
            entry, self.attribute, prediction, overwrite=self.overwrite)

    def apply_suggestion(self, entry, suggestions):
        """Add a list of suggestions to an entry."""
        if not self.attribute:
            raise NotImplementedError
        if suggestions:
            key = '__suggested_{}s__'.format(self.attribute)
            entry.meta[key] = suggestions
        return entry

    def process_transactions(
            self, transactions: List[Transaction]) -> List[Transaction]:
        """Process all imported transactions."""

        if not self.training_data:
            return transactions

        if self.predict:
            predictions = self.pipeline.predict(transactions)
            transactions = [
                self.apply_prediction(entry, payee)
                for entry, payee in zip(transactions, predictions)
            ]
            logger.debug("Added predictions to transactions.")

        if self.suggest:
            # Get values from the SVC decision function
            decision_values = self.pipeline.decision_function(transactions)

            # Add a human-readable class label to each value, and sort by
            # value:
            try:
                suggestions = [[
                    label for _, label in sorted(
                        list(zip(distance_values, self.pipeline.classes_)),
                        key=operator.itemgetter(0),
                        reverse=True)
                ] for distance_values in decision_values]
            except TypeError:
                suggestions = None

            # Add the suggestions to each transaction:
            if suggestions:
                transactions = [
                    self.apply_suggestion(entry, suggestion_list)
                    for entry, suggestion_list in zip(transactions,
                                                      suggestions)
                ]
            logger.debug("Added suggestions to transactions.")
        return transactions
