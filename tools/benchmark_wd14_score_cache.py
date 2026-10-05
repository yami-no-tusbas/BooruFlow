"""Compare deterministic synthetic WD14 payloads without opening any database."""

from __future__ import annotations

import json

from booruflow.infrastructure.wd14_score_cache import encode_wd14_scores


def main() -> None:
    labels = tuple(f"synthetic_descriptive_label_{index}" for index in range(10861))
    scores = {name: (0.75 if index < 24 else 0.000123456789)
              for index, name in enumerate(labels)}
    json_bytes = len(json.dumps(scores, separators=(",", ":")).encode())
    sparse_bytes = len(encode_wd14_scores(scores, labels))
    # Names with length prefixes, as used by the label hash. This is a logical
    # mapping payload estimate, not a prediction of SQLite pages/index overhead.
    shared_label_bytes = sum(4 + len(name.encode("utf-8")) for name in labels)
    count = 5999
    print(json.dumps({
        "labels": len(labels), "retained": 24,
        "json_bytes_per_vector": json_bytes, "sparse_bytes_per_vector": sparse_bytes,
        "vector_reduction_factor": json_bytes / sparse_bytes,
        "shared_label_payload_bytes": shared_label_bytes,
        "entries": count,
        "json_payload_mib": count * json_bytes / 2**20,
        "sparse_payload_mib": count * sparse_bytes / 2**20,
        "sparse_plus_one_label_payload_mib": (count * sparse_bytes + shared_label_bytes) / 2**20,
    }, indent=2))


if __name__ == "__main__":
    main()
