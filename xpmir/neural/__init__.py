import torch
import torch.nn as nn

from experimaestro import config, param
from xpmir.letor.samplers import Records, Collection
from xpmir.rankers import LearnableScorer
from xpmir.utils import easylog
from xpmir.vocab import Vocab


@param("qlen", default=20)
@param("dlen", default=2000)
@param(
    "add_runscore",
    default=False,
    help="Whether the base predictor score should be added to the model score",
)
@param("vocab", type=Vocab)
@param("collection", type=Collection, help="Collection access")
@config()
class EmbeddingScorer(LearnableScorer, nn.Module):
    def initialize(self, random):
        self.random = random
        self.logger = easylog()
        seed = self.random.randint((2 ** 32) - 1)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        if self.add_runscore:
            self.runscore_alpha = torch.nn.Parameter(torch.full((1,), -1.0))
        self.vocab.initialize()

    def _pad(self, sequences):
        return (
            self.vocab.pad_sequence(sequences, batch_first=True),
            torch.LongTensor([len(s) for s in sequences]),
        )

    def forward(self, inputs: Records):
        # Prepare inputs
        inputs.query_tok, inputs.query_len = self._pad(
            [[self.vocab.tok2id(t) for t in query] for query in inputs.queries]
        )
        raise NotImplementedError("Text is not tokenized!!!")

        documents = [self.collection.document_text(docid) for docid in inputs.docids]

        inputs.doc_tok, inputs.doc_len = self._pad(
            [
                [self.vocab.tok2id(t) for _, t in zip(range(self.dlen), document)]
                for document in documents
            ]
        )

        # Forward to model
        result = self._forward(inputs)

        if len(result.shape) == 2 and result.shape[1] == 1:
            result = result.reshape(result.shape[0])

        # Add run score if needed
        if self.add_runscore:
            alpha = torch.sigmoid(self.runscore_alpha)
            result = alpha * result + (1 - alpha) * inputs.runscore
        return result

    def _forward(self, **inputs):
        raise NotImplementedError

    def save(self, path):
        state = self.state_dict(keep_vars=True)
        for key in list(state):
            if state[key].requires_grad:
                state[key] = state[key].data
            else:
                del state[key]
        torch.save(state, path)

    def load(self, path):
        self.load_state_dict(torch.load(path), strict=False)
