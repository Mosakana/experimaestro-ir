import sys
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from experimaestro import Param, Config
from xpmir.letor.records import PointwiseRecords
from xpmir.letor.trainers import Metrics, Trainer


class PointwiseLoss(Config):
    NAME = "?"

    def compute(self, rel_scores, target_relscores) -> torch.Tensor:
        raise NotImplementedError()


class MSELoss(PointwiseLoss):
    NAME = "mse"

    def compute(self, rel_scores, target_relscores):
        return F.mse_loss(rel_scores, target_relscores)


class BinaryCrossEntropyLoss(PointwiseLoss):
    """Computes binary cross-entropy

    Attributes:
        logits: model outputs are logits (use a sigmoid)
    """

    NAME = "ce"

    logits: Param[bool] = True

    def __init__(self):
        self.loss = nn.BCEWithLogitsLoss() if self.logits else nn.BCELoss()

    def compute(self, rel_scores, target_relscores):
        return self.loss(rel_scores, (target_relscores > 0).float())


class PointwiseTrainer(Trainer):
    """Pointwise trainer

    Attribute:

        lossfn: Loss function to use (mse, mse-nil, l1, l1pos, smoothl1, cross_entropy, cross_entropy_logits, softmax, mean)
    """

    lossfn: Param[PointwiseLoss] = MSELoss()

    def initialize(self, random: np.random.RandomState, ranker, context):
        super().initialize(random, ranker, context)

        self.sampler.initialize(self.random)

        self.random = random
        self.train_iter_core = self.sampler.record_iter()
        self.train_iter = self.iter_batches(self.train_iter_core)

    def __validate__(self):
        assert self.grad_acc_batch >= 0, "Adaptative batch size not implemented"

    def iter_batches(self, it):
        while True:
            batch = PointwiseRecords()
            for _, record in zip(range(self.batch_size), it):
                batch.add(record)

            yield batch

    def train_batch(self, metrics: Metrics):
        # Get the next batch
        batch = next(self.train_iter)

        rel_scores = self.ranker(batch)
        if torch.isnan(rel_scores).any() or torch.isinf(rel_scores).any():
            self.logger.error("nan or inf relevance score detected. Aborting.")
            sys.exit(1)

        target_relscores = torch.FloatTensor(batch.relevances).to(self.device)

        # TODO: is this needed?
        target_relscores[
            target_relscores == -999.0
        ] = 0.0  # replace -999 with non-relevant score

        rel_scores = rel_scores.flatten()

        loss = self.lossfn.compute(rel_scores, target_relscores)

        # TODO: Create classes for the missing losses
        # Apply the loss
        # elif self.lossfn == "mse-nil":
        #     loss = F.mse_loss(
        #         rel_scores.flatten(), torch.zeros_like(rel_scores.flatten())
        #     )
        # elif self.lossfn == "l1":
        #     loss = F.l1_loss(rel_scores.flatten(), target_relscores)
        # elif self.lossfn == "l1pos":
        #     loss = F.l1_loss(rel_scores.flatten(), (target_relscores > 0.0).float())
        # elif self.lossfn == "smoothl1":
        #     loss = F.smooth_l1_loss(rel_scores.flatten(), target_relscores)
        # elif self.lossfn == "cross_entropy":
        #     loss = -torch.where(
        #         target_relscores > 0, rel_scores.flatten(), 1 - rel_scores.flatten()
        #     ).log()
        #     loss = loss.mean()
        # elif self.lossfn == "cross_entropy_logits":
        #     assert len(rel_scores.shape) == 2
        #     assert rel_scores.shape[1] == 2
        #     log_probs = -rel_scores.log_softmax(dim=1)
        #     one_hot = torch.tensor(
        #         [[1.0, 0.0] if tar > 0 else [0.0, 1.0] for tar in target_relscores],
        #         device=rel_scores.device,
        #     )
        #     loss = (log_probs * one_hot).sum(dim=1).mean()
        # elif self.lossfn == "softmax":
        #     assert len(rel_scores.shape) == 2
        #     assert rel_scores.shape[1] == 2
        #     probs = rel_scores.softmax(dim=1)
        #     one_hot = torch.tensor(
        #         [[0.0, 1.0] if tar > 0 else [1.0, 0.0] for tar in target_relscores],
        #         device=rel_scores.device,
        #     )
        #     loss = (probs * one_hot).sum(dim=1).mean()
        # elif self.lossfn == "mean":
        #     loss = rel_scores.mean()
        # else:
        #     raise ValueError(f"unknown lossfn `{self.lossfn}`")

        metrics.update({f"{self.lossfn}": loss.item()}, self.batch_size)
        return loss

    def fast_forward(self, record_count):
        self._fast_forward(self.train_iter_core, self.iter_fields, record_count)
