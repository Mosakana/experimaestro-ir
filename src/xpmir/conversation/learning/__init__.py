from functools import cached_property
import numpy as np
from datamaestro_text.data.ir import TopicRecord
from datamaestro_text.data.conversation import (
    ConversationDataset,
    ConversationHistoryItem,
    TopicConversationRecord,
)
from experimaestro import Param

from xpmir.learning.base import BaseSampler
from xpmir.utils.iter import RandomSerializableIterator


class DatasetConversationEntrySampler(BaseSampler):
    """Uses a conversation dataset and topic records entries"""

    dataset: Param[ConversationDataset]
    """The conversation dataset"""

    @cached_property
    def conversations(self):
        return list(self.dataset.__iter__())

    def __iter__(self) -> RandomSerializableIterator[TopicConversationRecord]:
        def generator(random: np.random.RandomState):
            while True:
                # Pick a random conversation
                conversation_ix = random.randint(0, len(self.conversations))
                conversation = self.conversations[conversation_ix]

                # Pick a random topic record entry
                nodes = [
                    node
                    for node in conversation
                    if isinstance(node.entry(), TopicRecord)
                ]
                node_ix = random.randint(len(nodes))
                node = nodes[node_ix]

                node = node.entry().add(
                    ConversationHistoryItem(node.history()), no_check=True
                )

                yield node

        return RandomSerializableIterator(self.random, generator)
