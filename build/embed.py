"""
Embed the paintings with DINOv2, a self-supervised vision model that goes by layout, shapes and colours, and write the
"look" embedding column. Adapted from knack-countries' build/embed.py.
  python embed.py [folder]      (needs numpy, torch, transformers, pillow; folder defaults to ..)

Reads each row's picture (media/<id>.jpg), writes the full-size vectors to build/look-full.npy (not a column) and the
64-number version to <folder>/look.npy, and sets the column in <folder>/columns.json. The 64-number version is made as
the Knack app does (src/lib/engine/embed.ts, reduce): unit-length vectors, centre, PCA to 64 numbers, scale so the
mean squared norm is 1.

The whole picture is shown to the model, squashed to 224x224 (no centre crop, which would cut off the ends of a wide
screen or a tall scroll). The class token (final layer norm) is used.
"""
import json, sys
from pathlib import Path
import numpy as np, torch
from PIL import Image
from transformers import AutoModel

HERE = Path(__file__).resolve().parent
COLUMN, FILE, MODEL, OF = 'look', 'look.npy', 'facebook/dinov2-base', 'picture'
MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]


def reduce(v, dims=64):
    u = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    z = (u - u.mean(0)).astype(np.float64)
    _, _, vt = np.linalg.svd(z, full_matrices=False)
    p = z @ vt[:dims].T
    return (p / np.sqrt((p ** 2).sum(1).mean())).astype(np.float32)


def load(paths):
    ims = [np.asarray(Image.open(p).convert('RGB').resize((224, 224), Image.BICUBIC), dtype=np.float32) / 255 for p in paths]
    x = (np.stack(ims) - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    return torch.from_numpy(x).permute(0, 3, 1, 2)


@torch.no_grad()
def run(paths):
    model = AutoModel.from_pretrained(MODEL).eval()
    return np.concatenate([model(pixel_values=load(paths[s:s + 16])).pooler_output.numpy() for s in range(0, len(paths), 16)])


if __name__ == '__main__':
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent
    rows = [json.loads(l) for l in open(root / 'rows.jsonl') if l.strip()]
    meta = json.load(open(root / 'columns.json'))
    full = run([root / r[OF] for r in rows])
    np.save(HERE / 'look-full.npy', full.astype(np.float32))
    np.save(root / FILE, reduce(full))
    # what makes two of the column's values alike is written on the column itself ("near"), never as a column of
    # its own
    by = COLUMN[len(OF) + 1:] if COLUMN.startswith(OF + ' ') else COLUMN
    near = {'file': FILE, 'by': by, 'model': MODEL}
    meta['columns'] = [c for c in meta['columns'] if c['name'] != COLUMN]
    for c in meta['columns']:
        if c['name'] == OF:
            others = [x for x in ([c['near']] if isinstance(c.get('near'), dict) else c.get('near', [])) if x.get('file') != FILE]
            c['near'] = [*others, near] if others else near
    with open(root / 'columns.json', 'w') as f: f.write(json.dumps(meta, indent=1, ensure_ascii=False) + '\n')
    print(COLUMN, full.shape, '->', FILE, flush=True)
