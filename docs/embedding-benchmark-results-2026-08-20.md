# Embedding benchmark — first local smoke run (2026-08-20)

This is not a model-selection result. The local sample contains only 31 deduplicated images with
20/6/2/2/1 images per artist. Artist labels come from filenames and content labels from parent folder
names. No manual style judgment has yet been recorded.

| Backend | Model | Dim. | Model size | CPU/image | CUDA/image | CUDA load | Approx. VRAM delta | R@1 | R@5 | R@10 | MRR | Cross-subject MRR | Content leakage | Weighted coherence | Licence |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| OpenCLIP | ViT-B/32 LAION-2B s34B b79K | 512 | 605.1 MB | 0.0761 s | 0.0126 s | 7.01 s | 748 MiB | 0.774 | 0.935 | 0.968 | 0.851 | 0.742 | 0.000* | 0.870 | MIT code; checkpoint/data terms to retain |
| Author_ID embedding | ConvNeXt-Tiny normalized pre-centroid output | 512 | 117.7 MB | 0.1024 s | 0.0194 s | 0.69 s | 342 MiB | 0.871 | 0.935 | 0.968 | 0.894 | 0.873 | 0.000* | 0.522 | Apache-2.0 |
| WD v3 intermediate | Current WD ViT tagger | — | 378.5 MB | — | — | — | — | — | — | — | — | — | — | — | Apache-2.0 |
| Anime Images Style Embedder v4 | DINOv3 CLS + MLP | 6/7 | — | — | — | — | — | — | — | — | — | — | — | — | MIT, plus gated DINOv3 terms |
| Jina CLIP v2 | EVA02-L vision tower | 64–1024 | — | — | — | — | — | — | — | — | — | — | — | — | CC-BY-NC-4.0 |

`*` Content leakage is not trustworthy here: parent-folder labels are sparse proxies, not complete
post tags. Coherence values are not directly comparable across embedding spaces and are heavily
affected by singleton/two-image artists.

GPU timing excludes one explicit warm-up inference. Warm-up was 0.196 s for OpenCLIP and 1.782 s
for Author_ID. Initial OpenCLIP checkpoint download took about 55.5 s and is excluded from cached
load time. The derived Author_ID ONNX only exposes an existing normalized node; it does not change
weights or operations.

## Query 7 example (Butterchalk)

OpenCLIP Top-10 artist sequence:

```text
Andava, Butterchalk, Andava, Butterchalk, Butterchalk,
Photonoko, Butterchalk, Butterchalk, Butterchalk, Butterchalk
```

OpenCLIP's best individual image is Andava (0.7997), while artist-centroid ranking puts Butterchalk
first (0.8504), followed by Andava (0.8213). This demonstrates why maximum-image similarity is
biased/noisy and centroid or balanced Top-K aggregation should remain visible.

Author_ID Top-10 contains ten Butterchalk images. Its Butterchalk centroid similarity is 0.5220;
the next artist centroid is Geister at 0.0458. This is encouraging for this query but must be checked
on a larger balanced corpus and against manual style-only/content-decoy judgments.

Local comparison galleries:

- `var/experiments/openclip-query-7.html`
- `var/experiments/author-id-query-7.html`
