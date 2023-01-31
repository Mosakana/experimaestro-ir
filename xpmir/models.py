from pathlib import Path
from typing import Optional, Union
from experimaestro.huggingface import ExperimaestroHFHub
from experimaestro import Config
from xpmir.neural.dual import DotDense
from xpmir.utils.utils import easylog
import importlib

logger = easylog()


def get_class(name: str):
    module_name, class_name = name.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


class XPMIRHFHub(ExperimaestroHFHub):
    def __init__(
        self,
        config: Config,
        variant: Optional[str] = None,
        readme: Optional[str] = None,
    ):
        super().__init__(config, variant)
        self.readme = readme

    def _save_pretrained(self, save_directory: Union[str, Path]):
        save_directory = Path(save_directory)
        super()._save_pretrained(save_directory)
        if self.readme:
            (save_directory / "README.md").write_text(self.readme)


class AutoModel:
    @staticmethod
    def load_from_hf_hub(hf_id_or_folder: str, variant: Optional[str] = None):
        """Loads from hugging face hub or from a folder"""
        return XPMIRHFHub.from_pretrained(hf_id_or_folder, variant=variant)

    @staticmethod
    def push_to_hf_hub(config: Config, *args, variant=None, readme=None, **kwargs):
        """Push to HuggingFace Hub

        See ModelHubMixin.push_to_hub for the other arguments
        """
        return XPMIRHFHub(config, variant=variant, readme=readme).push_to_hub(
            *args, **kwargs
        )

    @staticmethod
    def sentence_scorer(hf_id: str):
        """Loads from hugging face hub using a sentence transformer"""
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            logger.error(
                "Sentence transformer is not installed:"
                "pip install -U sentence_transformers"
            )
            raise

        encoder = SentenceTransformer(hf_id)
        return DotDense(encoder=encoder)
