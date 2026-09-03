# Visual/style embedding experiment

This tool is deliberately separate from the production Image Analysis pipeline. Its cache lives at
`var/experiments/embeddings.sqlite`; vectors from different backend identities are never compared.

## Dataset manifest

CSV columns:

```csv
path,artist,tags,groups
D:/images/example.png,artist_name,"tag_a tag_b","same_artist content_decoy"
```

`artist`, `tags`, and `groups` are manual benchmark metadata. No named artist has a built-in judgment.
Resolved images can instead be imported from `image_analysis.sqlite`; a single source tag categorized
as `artist` is used when unambiguous.

```powershell
python -m booruflow.cli.embedding_benchmark import-manifest dataset.csv
python -m booruflow.cli.embedding_benchmark import-image-analysis var/state/image_analysis.sqlite
python -m booruflow.cli.embedding_benchmark label 12 --artist example_artist --tags "anthro casual"
```

## Encoding and review

OpenCLIP is optional and is not a BooruFlow dependency:

```powershell
python -m booruflow.cli.embedding_benchmark encode --backend openclip --maximum-per-artist 50 --seed 42
python -m booruflow.cli.embedding_benchmark evaluate --backend openclip
python -m booruflow.cli.embedding_benchmark neighbors 12 --backend openclip --limit 20
python -m booruflow.cli.embedding_benchmark gallery 12 --backend openclip --limit 20 --output var/experiments/gallery.html
python -m booruflow.cli.embedding_benchmark judge 12 34 style_only --note "manual comparison"
```

Accepted human labels are `strongly_similar`, `style_only`, `interesting_different`, and
`false_positive`. They are comparison evidence only and are not used for training.

## Metrics

- Same-artist retrieval excludes the query and reports Recall@1/5/10 and MRR.
- Cross-subject MRR considers same-artist candidates whose content-tag Jaccard overlap is at most
  0.20 after removing a small, explicit generic-tag set.
- Content leakage compares the best low-overlap same-artist candidate with other-artist candidates
  whose content overlap is at least 0.50. The rate is the fraction where the content decoy wins.
- Artist coherence is mean cosine similarity to the normalized artist centroid. Dispersion is mean
  cosine distance and distance variance is kept separately.
- Distinctiveness is one minus the closest other-artist centroid similarity.
- Artist results expose best image, mean Top-K, and centroid similarity separately. Centroid ranking
  is the least sensitive to corpus size; balanced deterministic sampling remains required for fair
  benchmarks.

Thresholds and the generic-tag set are experimental constants, intentionally visible and revisable.
Multi-style clustering is not inferred automatically; optional group labels allow users to preserve
known substyles until enough observations justify clustering.

## Candidate audit (2026-08-20)

| Candidate | Status | Embedding | Dependencies / risk | Licence |
|---|---|---:|---|---|
| OpenCLIP ViT-B/32 | Optional baseline implemented | 512 | PyTorch + `open_clip_torch`; checkpoint download | OpenCLIP code MIT; verify selected checkpoint/data terms |
| WD v3 intermediate | Research only | Unknown | Current ONNX exposes tagging output; no intermediate representation has yet been validated as a stable public output | Apache-2.0 model repository |
| Anime Images Style Embedder v4 | Deferred adapter | 6/7 | PyTorch; gated DINOv3 access and Hugging Face token; older generations differ materially | MIT repository |
| Author_ID | Optional ONNX adapter implemented | 512 | A derived experimental copy exposes the existing normalized `/backbone/Div_output_0` before the fixed centroids and Top-K; source weights are unchanged | Apache-2.0 |
| Jina CLIP v2 | Deferred | 64–1024 | 0.9B parameters, remote custom code or large ONNX, likely poor fit for 6 GiB comparison phase | CC-BY-NC-4.0 |

Do not use final class logits as a substitute for an intermediate style embedding. Do not select a
production backend before a manually labelled, cross-subject dataset has been evaluated.

## First smoke dataset

An initial deduplicated sample was built from artist names already present in local filenames. It has
31 images distributed as 20/6/2/2/1 across five artists. Parent-folder names are only weak content
labels. This sample validates the machinery and can reveal examples, but its quality metrics must not
be treated as a model-selection result. A balanced manually labelled corpus remains required.
