import logging
import json
from pathlib import Path
from typing import Dict, Iterator
from datamaestro_text.data.ir import Adhoc
from experimaestro import (
    Param,
    copyconfig,
    pathgenerator,
    Annotated,
)
import numpy as np
from xpmir.utils.utils import easylog
from xpmir.evaluation import evaluate
from xpmir.learning.context import (
    TrainState,
    TrainerContext,
)
from xpmir.rankers import (
    Retriever,
)
from xpmir.learning.learner import LearnerListener, Learner, LearnerListenerStatus
from xpmir.index.faiss import DynamicFaissIndex

logger = easylog()


class ValidationListener(LearnerListener):
    """Learning validation early-stopping
    Computes a validation metric and stores the best result. If early_stop is
    set (> 0), then it signals to the learner that the learning process can
    stop.
    """

    metrics: Param[Dict[str, bool]] = {"map": True}
    """Dictionary whose keys are the metrics to record, and boolean
            values whether the best performance checkpoint should be kept for
            the associated metric ([parseable by ir-measures](https://ir-measur.es/))"""

    dataset: Param[Adhoc]
    """The dataset to use"""

    retriever: Param[Retriever]
    """The retriever for validation"""

    warmup: Param[int] = -1
    """How many epochs before actually computing the metric"""

    bestpath: Annotated[Path, pathgenerator("best")]
    """Path to the best checkpoints"""

    last_checkpoint_path: Annotated[Path, pathgenerator("last_checkpoint")]
    """Path to the last checkpoints"""

    store_last_checkpoint: Param[bool] = False
    """Besides the model with the best performance, whether store the last
    checkpoint of the model or not"""

    info: Annotated[Path, pathgenerator("info.json")]
    """Path to the JSON file that contains the metric values at each epoch"""

    validation_interval: Param[int] = 1
    """Epochs between each validation"""

    early_stop: Param[int] = 0
    """Number of epochs without improvement after which we stop learning.
    Should be a multiple of validation_interval or 0 (no early stopping)"""

    def __validate__(self):
        assert (
            self.early_stop % self.validation_interval == 0
        ), "Early stop should be a multiple of the validation interval"

    def initialize(self, learner: Learner, context: TrainerContext):
        super().initialize(learner, context)

        self.retriever.initialize()
        self.bestpath.mkdir(exist_ok=True, parents=True)
        if self.store_last_checkpoint:
            self.last_checkpoint_path.mkdir(exist_ok=True, parents=True)

        # Checkpoint start
        try:
            with self.info.open("rt") as fp:
                self.top = json.load(fp)  # type: Dict[str, Dict[str, float]]
        except Exception:
            self.top = {}

    def update_metrics(self, metrics: Dict[str, float]):
        if self.top:
            # Just use another key
            for metric in self.metrics.keys():
                metrics[f"{self.id}/final/{metric}"] = self.top[metric]["value"]

    def monitored(self) -> Iterator[str]:
        return [key for key, monitored in self.metrics.items() if monitored]

    def taskoutputs(self, learner: "Learner"):
        """Experimaestro outputs: returns the best checkpoints for each
        metric"""
        res = {
            key: copyconfig(learner.model, checkpoint=str(self.bestpath / key))
            for key, store in self.metrics.items()
            if store
        }
        if self.store_last_checkpoint:
            res["last_checkpoint"] = copyconfig(
                learner.scorer, checkpoint=str(self.last_checkpoint_path)
            )

        return res

    def should_stop(self, epoch=0):
        if self.early_stop > 0 and self.top:
            epochs_since_imp = (epoch or self.context.epoch) - max(
                info["epoch"] for key, info in self.top.items() if self.metrics[key]
            )
            if epochs_since_imp >= self.early_stop:
                return LearnerListenerStatus.STOP

        return LearnerListenerStatus.DONT_STOP

    def __call__(self, state: TrainState):
        # Check that we did not stop earlier (when loading from checkpoint / if other
        # listeners have not stopped yet)
        if self.should_stop(state.epoch - 1) == LearnerListenerStatus.STOP:
            return LearnerListenerStatus.STOP

        if state.epoch % self.validation_interval == 0:
            # Compute validation metrics
            means, details = evaluate(
                self.retriever, self.dataset, list(self.metrics.keys()), True
            )

            for metric, keep in self.metrics.items():
                value = means[metric]

                self.context.writer.add_scalar(
                    f"{self.id}/{metric}/mean", value, state.step
                )

                self.context.writer.add_histogram(
                    f"{self.id}/{metric}",
                    np.array(list(details[metric].values()), dtype=np.float32),
                    state.step,
                )

                # Update the top validation
                if state.epoch >= self.warmup:
                    topstate = self.top.get(metric, None)
                    if topstate is None or value > topstate["value"]:
                        # Save the new top JSON
                        self.top[metric] = {"value": value, "epoch": self.context.epoch}

                        # Copy in corresponding directory
                        if keep:
                            logging.info(
                                f"Saving the checkpoint {state.epoch}"
                                f" for metric {metric}"
                            )
                            self.context.copy(self.bestpath / metric)

            if self.store_last_checkpoint:
                logging.info(f"Saving the last checkpoint {state.epoch}")
                self.context.copy(self.last_checkpoint_path)

            # Update information
            with self.info.open("wt") as fp:
                json.dump(self.top, fp)

        # Early stopping?
        return self.should_stop()


class NegativeSamplerListener(LearnerListener):

    sampling_interval: Param[int] = 128
    """During how many epochs we recompute the negatives"""

    def initialize(self, learner: "Learner", context: TrainerContext):
        self.change = True
        super().initialize(learner, context)
        self.sampler_index = 0

    def __call__(self, state: TrainState) -> bool:

        if state.epoch % self.sampling_interval == 0:
            if self.change:  # First time to change the sampler
                self.sampler_index += 1
                state.trainer.sampler.pairwise_iter().set_current(self.sampler_index)
                self.change = False
            state.trainer.sampler.samplers[self.sampler_index].update()

        return LearnerListenerStatus.NO_DECISION

    def update_metrics(self, metrics: Dict[str, float]):
        pass

    def taskoutputs(self, learner: "Learner"):
        pass


class FaissBuildListener(LearnerListener):

    indexing_interval: Param[int] = 128
    """During how many epochs we recompute the index"""

    indexbackedfaiss: Param[DynamicFaissIndex]
    """The faiss object"""

    def initialize(self, learner: "Learner", context: TrainerContext):
        super().initialize(learner, context)

    def __call__(self, state: TrainState) -> bool:

        if state.epoch % self.indexing_interval == 0:

            # state.path = 'checkpoint/epoch-00000XX/'
            path = state.path / "listeners" / self.id
            path.mkdir(exist_ok=True, parents=True)
            path = path / "faiss.dat"
            self.indexbackedfaiss.update(path)

        return LearnerListenerStatus.NO_DECISION

    def update_metrics(self, metrics: Dict[str, float]):
        pass

    def taskoutputs(self, learner: "Learner"):
        pass
