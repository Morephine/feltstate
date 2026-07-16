# Third-party notices

The core of `feltstate` is pure standard library and pulls in nothing. This file
covers the *optional* `[vheart]` extra, which loads a fine-tuned model and its
Python stack, and the reference model artifacts that `VheartSource` is meant to
load. It is provided for transparency; it is **not** legal advice, and it does
not grant you any rights in the third-party software or model weights listed
below. When you enable the extra, you are responsible for reviewing and
complying with each upstream license and model card.

## Model artifacts loaded by `VheartSource`

`feltstate.sources.vheart.VheartSource` does not bundle any weights. It downloads
the adapter repo id (and its base model) you pass at runtime from the Hugging
Face Hub.

### Illustrative adapters referenced by the library and examples

Two adapters are referenced in the examples and the README. These weights are
**not distributed with this library**; they are downloaded from the Hugging Face
Hub at runtime when you construct a `VheartSource` that points at them.

| Role | Hub repo id | License |
| --- | --- | --- |
| LoRA adapter (1.5B base) | [`kaishuiji/vheart-affect-v8`](https://huggingface.co/kaishuiji/vheart-affect-v8) | Apache-2.0 |
| LoRA adapter (4B base) | [`kaishuiji/vheart-affect-v9`](https://huggingface.co/kaishuiji/vheart-affect-v9) | Apache-2.0 |

The base model for each adapter is declared in the adapter's `adapter_config.json`
on the Hub and is read by `VheartSource` at construction time. The known base
models are:

| Adapter | Base model | Base license |
| --- | --- | --- |
| `kaishuiji/vheart-affect-v8` | [`Qwen/Qwen2.5-1.5B-Instruct`](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) | Apache-2.0 |
| `kaishuiji/vheart-affect-v9` | [`Qwen/Qwen3-4B`](https://huggingface.co/Qwen/Qwen3-4B) | Apache-2.0 |

Check each adapter's model card for the exact pinned base model id and its
license before use, as model cards are the authoritative source.

Recommended: pin an immutable commit SHA with `revision=` (adapter) and
`base_revision=` (base) rather than tracking a mutable branch such as `"main"`.
See `VheartSource`'s docstring and the `SECURITY.md` supply-chain section.

### Notes for any adapter you load

- A LoRA adapter is a small set of weights applied *on top of* a base model. Both
  the adapter **and** its base carry their own licenses and acceptable-use terms;
  the base model's terms flow through to any adapter built on it.
- Many popular base checkpoints ship under permissive licenses (e.g. Apache-2.0
  or MIT), but others use bespoke community/research licenses with use
  restrictions. Do not assume — check the specific base model's card.
- Training data for the adapters above is not distributed with this project.

## Python dependencies of the `[vheart]` extra

Installed only when you opt in with `pip install "feltstate[vheart]"`. Each is a
separate project under its own license; the summaries below are informational —
verify against the version you install.

| Package | Typical license |
| --- | --- |
| `torch` | BSD-3-Clause |
| `transformers` | Apache-2.0 |
| `peft` | Apache-2.0 |
| `huggingface_hub` | Apache-2.0 |

The `feltstate` project itself is licensed under the MIT License (see `LICENSE`).
