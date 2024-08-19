import json
from pathlib import Path
from typing import Any

import numpy as np
import tensorflow as tf
from loguru import logger

from kdp.features import CategoricalFeature, FeatureType, NumericalFeature


class WelfordAccumulator:
    """Accumulator for computing the mean and variance of a sequence of numbers
    using the Welford algorithm (streaming data).
    """

    def __init__(self):
        """Initializes the accumulators for the Welford algorithm."""
        self.n = tf.Variable(
            0.0,
            dtype=tf.float32,
            trainable=False,
        )
        self.mean = tf.Variable(
            0.0,
            dtype=tf.float32,
            trainable=False,
        )
        self.M2 = tf.Variable(
            0.0,
            dtype=tf.float32,
            trainable=False,
        )
        self.var = tf.Variable(
            0.0,
            dtype=tf.float32,
            trainable=False,
        )

    @tf.function
    def update(self, values: tf.Tensor) -> None:
        """Updates the accumulators with new values using the Welford algorithm.

        Args:
            values: The new values to add to the accumulators.
        """
        values = tf.cast(values, tf.float32)
        n = self.n + tf.cast(tf.size(values), tf.float32)
        delta = values - self.mean
        self.mean.assign(self.mean + tf.reduce_sum(delta / n))
        self.M2.assign(self.M2 + tf.reduce_sum(delta * (values - self.mean)))
        self.n.assign(n)

    @property
    def variance(self) -> float:
        """Returns the variance of the accumulated values."""
        return self.M2 / (self.n - 1) if self.n > 1 else self.var

    @property
    def count(self) -> int:
        """Returns the number of accumulated values."""
        return self.n


class CategoricalAccumulator:
    def __init__(self) -> None:
        """Initializes the accumulator for categorical values."""
        # Using a single accumulator since tf.string can hold both strings and bytes
        self.values = tf.Variable(
            [],
            dtype=tf.string,
            shape=tf.TensorShape(None),
            trainable=False,
        )
        self.int_values = tf.Variable(
            [],
            dtype=tf.int32,
            shape=tf.TensorShape(None),
            trainable=False,
        )

    @tf.function
    def update(self, new_values: tf.Tensor) -> None:
        """Updates the accumulator with new categorical values.

        Args:
            new_values: The new categorical values to add to the accumulator.
        """
        if new_values.dtype == tf.string:
            updated_values = tf.unique(tf.concat([self.values, new_values], axis=0))[0]
            self.values.assign(updated_values)
        elif new_values.dtype == tf.int32:
            updated_values = tf.unique(tf.concat([self.int_values, new_values], axis=0))[0]
            self.int_values.assign(updated_values)
        else:
            raise ValueError(f"Unsupported data type for categorical features: {new_values.dtype}")

    def get_unique_values(self) -> list:
        """Returns the unique categorical values accumulated so far."""
        all_values = tf.concat([self.values, tf.strings.as_string(self.int_values)], axis=0)
        return tf.unique(all_values)[0].numpy().tolist()


class TextAccumulator:
    def __init__(self) -> None:
        """Initializes the accumulator for text values, where each entry is a list of words separated by spaces.

        Attributes:
            words (tf.Variable): TensorFlow variable to store unique words as strings.
        """
        self.words = tf.Variable(
            [],
            dtype=tf.string,
            shape=tf.TensorShape(None),
            trainable=False,
        )
        logger.info("TextAccumulator initialized.")

    @tf.function
    def update(self, new_texts: tf.Tensor) -> None:
        """Updates the accumulator with new text values, extracting words and accumulating unique ones.

        Args:
            new_texts: A batch of text values (tf.Tensor of dtype tf.string),
            each entry containing words separated by spaces.

        Raises:
            ValueError: If the input tensor is not of dtype tf.string.
        """
        if new_texts.dtype != tf.string:
            raise ValueError(f"Unsupported data type for text features: {new_texts.dtype}")

        # Split each string into words and flatten the list
        new_texts = tf.strings.regex_replace(new_texts, r"\s+", " ")
        split_words = tf.strings.split(new_texts).flat_values
        split_words = tf.strings.lower(split_words)

        # Concatenate new words with existing words and update unique words
        updated_words = tf.unique(tf.concat([self.words, split_words], axis=0))[0]
        self.words.assign(updated_words)

    def get_unique_words(self) -> list:
        """Returns the unique words accumulated so far as a list of strings.

        Returns:
            list of str: Unique words accumulated.
        """
        unique_words = self.words.value().numpy().tolist()
        return unique_words


class DateAccumulator:
    """Accumulator for computing statistics of date features including cyclical encoding."""

    def __init__(self):
        """Initializes the accumulators for date features."""
        # For year, month, and day of the week
        self.year_accumulator = WelfordAccumulator()
        self.month_sin_accumulator = WelfordAccumulator()
        self.month_cos_accumulator = WelfordAccumulator()
        self.day_of_week_sin_accumulator = WelfordAccumulator()
        self.day_of_week_cos_accumulator = WelfordAccumulator()

    @tf.function
    def update(self, dates: tf.Tensor) -> None:
        """Updates the accumulators with new date values.

        Args:
            dates: A tensor of shape [batch_size, 3] where each row contains [year, month, day_of_week].
        """
        year = dates[:, 0]
        month = dates[:, 1]
        day_of_week = dates[:, 2]

        # Cyclical encoding
        pi = tf.math.pi
        month_sin = tf.math.sin(2 * pi * month / 12)
        month_cos = tf.math.cos(2 * pi * month / 12)
        day_of_week_sin = tf.math.sin(2 * pi * day_of_week / 7)
        day_of_week_cos = tf.math.cos(2 * pi * day_of_week / 7)

        self.year_accumulator.update(year)
        self.month_sin_accumulator.update(month_sin)
        self.month_cos_accumulator.update(month_cos)
        self.day_of_week_sin_accumulator.update(day_of_week_sin)
        self.day_of_week_cos_accumulator.update(day_of_week_cos)

    @property
    def mean(self) -> dict:
        """Returns the mean statistics for date features."""
        return {
            "year": self.year_accumulator.mean.numpy(),
            "month_sin": self.month_sin_accumulator.mean.numpy(),
            "month_cos": self.month_cos_accumulator.mean.numpy(),
            "day_of_week_sin": self.day_of_week_sin_accumulator.mean.numpy(),
            "day_of_week_cos": self.day_of_week_cos_accumulator.mean.numpy(),
        }

    @property
    def variance(self) -> dict:
        """Returns the variance statistics for date features."""
        return {
            "year": self.year_accumulator.variance.numpy(),
            "month_sin": self.month_sin_accumulator.variance.numpy(),
            "month_cos": self.month_cos_accumulator.variance.numpy(),
            "day_of_week_sin": self.day_of_week_sin_accumulator.variance.numpy(),
            "day_of_week_cos": self.day_of_week_cos_accumulator.variance.numpy(),
        }


class DatasetStatistics:
    def __init__(
        self,
        path_data: str,
        features_specs: dict[str, FeatureType | str] = None,
        numeric_features: list[NumericalFeature] = None,
        categorical_features: list[CategoricalFeature] = None,
        text_features: list[CategoricalFeature] = None,
        date_features: list[str] = None,
        features_stats_path: Path = None,
        overwrite_stats: bool = False,
        batch_size: int = 50_000,
    ) -> None:
        """Initializes the statistics accumulators for numeric, categorical, text, and date features.

        Args:
            path_data: Path to the folder containing the CSV files.
            batch_size: The batch size to use when reading data from the dataset.
            features_stats_path: Path to the features statistics JSON file (defaults to None).
            overwrite_stats: Whether or not to overwrite existing statistics file (defaults to False).
            features_specs:
                A dictionary mapping feature names to feature specifications (defaults to None).
                Easier alternative to providing numerical and categorical lists.
            numeric_features: A list of numerical features to calculate statistics for (defaults to None).
            categorical_features: A list of categorical features to calculate statistics for (defaults to None).
            text_features: A list of text features to calculate statistics for (defaults to None).
            date_features: A list of date features to calculate statistics for (defaults to None).
        """
        self.path_data = path_data
        self.numeric_features = numeric_features or []
        self.categorical_features = categorical_features or []
        self.text_features = text_features or []
        self.date_features = date_features or []
        self.features_specs = features_specs or {}
        self.features_stats_path = features_stats_path or "features_stats.json"
        self.overwrite_stats = overwrite_stats
        self.batch_size = batch_size

        # Initializing placeholders for statistics
        self.numeric_stats = {col: WelfordAccumulator() for col in self.numeric_features}
        self.categorical_stats = {col: CategoricalAccumulator() for col in self.categorical_features}
        self.text_stats = {col: TextAccumulator() for col in self.text_features}
        self.date_stats = {col: DateAccumulator() for col in self.date_features}

    def _process_batch(self, batch: tf.Tensor) -> None:
        """Update statistics accumulators for each batch.

        Args:
            batch: A batch of data from the dataset.
        """
        for feature in self.numeric_features:
            self.numeric_stats[feature].update(batch[feature])

        for feature in self.categorical_features:
            self.categorical_stats[feature].update(batch[feature])

        for feature in self.text_features:
            self.text_stats[feature].update(batch[feature])

        for feature in self.date_features:
            self.date_stats[feature].update(batch[feature])

    def _compute_final_statistics(self) -> dict[str, dict]:
        """Compute final statistics for numeric, categorical, text, and date features."""
        logger.info("Computing final statistics for all features 📊")
        final_stats = {
            "numeric_stats": {},
            "categorical_stats": {},
            "text_stats": {},
            "date_stats": {},
        }
        for feature in self.numeric_features:
            final_stats["numeric_stats"][feature] = {
                "mean": self.numeric_stats[feature].mean.numpy(),
                "count": self.numeric_stats[feature].count.numpy(),
                "var": self.numeric_stats[feature].variance.numpy(),
                "dtype": self.features_specs[feature].dtype,
            }

        for feature in self.categorical_features:
            _dtype = self.features_specs[feature].dtype
            if _dtype == tf.int32:
                unique_values = [int(_byte) for _byte in self.categorical_stats[feature].get_unique_values()]
                unique_values.sort()
            else:
                _unique_values = self.categorical_stats[feature].get_unique_values()
                unique_values = [(_byte).decode("utf-8") for _byte in _unique_values]
            final_stats["categorical_stats"][feature] = {
                "size": len(unique_values),
                "vocab": unique_values,
                "dtype": _dtype,
            }

        for feature in self.text_features:
            unique_words = self.text_stats[feature].get_unique_words()
            final_stats["text_stats"][feature] = {
                "size": len(unique_words),
                "vocab": unique_words,
                "dtype": self.features_specs[feature].dtype,
            }

        for feature in self.date_features:
            # init stats dates
            final_stats["date_stats"][feature] = {}

            # adding means stats
            _means_data: dict = self.date_stats[feature].mean
            for feat_name in _means_data:
                final_stats["date_stats"][feature][f"mean_{feat_name}"] = _means_data.get("feat_name", 0)

            # adding var stats
            _vars_data: dict = self.date_stats[feature].variance
            for feat_name in _vars_data:
                final_stats["date_stats"][feature][f"mean_{feat_name}"] = _vars_data.get("feat_name", 0)

        return final_stats

    def calculate_dataset_statistics(self, dataset: tf.data.Dataset) -> dict[str, dict]:
        """Calculates and returns statistics for the dataset.

        Args:
            dataset: The dataset for which to calculate statistics.
        """
        logger.info("Calculating statistics for the dataset 📊")
        for batch in dataset:
            self._process_batch(batch)

        # calculating data statistics
        self.features_stats = self._compute_final_statistics()

        return self.features_stats

    @staticmethod
    def _custom_serializer(obj) -> Any:
        """Custom JSON serializer for objects not serializable by default json code."""
        if isinstance(obj, tf.dtypes.DType):
            return obj.name  # Convert dtype to its string representation
        elif isinstance(obj, np.integer):
            return int(obj)  # Convert numpy int to Python int
        elif isinstance(obj, np.floating):
            return float(obj)  # Convert numpy float to Python float
        elif isinstance(obj, bytes):
            return str(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()  # Convert numpy arrays to lists
        logger.debug(f"Type {type(obj)} is not serializable")
        raise TypeError("Type not serializable")

    def _save_stats(self) -> None:
        """Saving feature stats locally."""
        logger.info(f"Saving feature stats locally to: {self.features_stats_path}")

        # Convert the string path to a Path object before calling open
        path_obj = Path(self.features_stats_path)
        with path_obj.open("w") as f:
            json.dump(self.features_stats, f, default=self._custom_serializer)
        logger.info("features_stats saved ✅")

    def _load_stats(self) -> dict:
        """Loads serialized features stats from a file, with custom handling for TensorFlow dtypes.

        Returns:
            A dictionary containing the loaded features statistics.
        """
        if self.overwrite_stats:
            logger.info("overwrite_stats is currently active ⚙️")
            return {}

        stats_path = Path(self.features_stats_path)
        if stats_path.is_file():
            logger.info(f"Found columns statistics, loading as features_stats: {self.features_stats_path}")
            with stats_path.open() as f:
                self.features_stats = json.load(f)

            # Convert dtype strings back to TensorFlow dtype objects
            for stats_type in self.features_stats.values():  # 'numeric_stats' and 'categorical_stats'
                for _, feature_stats in stats_type.items():
                    if "dtype" in feature_stats:
                        feature_stats["dtype"] = tf.dtypes.as_dtype(feature_stats["dtype"])
            logger.info("features_stats loaded ✅")
        else:
            logger.info("No serialized features stats were detected ...")
            self.features_stats = {}
        return self.features_stats

    def main(self) -> dict:
        """Calculates and returns final statistics for the dataset.

        Resturns:
            A dictionary containing the calculated statistics for the dataset.
        """
        ds = self._read_data_into_dataset()
        stats = self.calculate_dataset_statistics(dataset=ds)
        self._save_stats()
        return stats
