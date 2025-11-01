# unbun

`unbun` is a tiny Python utility that extracts the embedded JavaScript bundles
from Bun standalone executables (including Claude Desktop releases). Instead of
guessing offsets, it parses Bun’s `StandaloneModuleGraph` footer and dumps the
exact files that were packaged with the binary.

## Requirements

- Python 3.9 or newer.
- [Prettier](https://prettier.io/) CLI available in `PATH` (optional). If it is
  installed, `unbun` will run Prettier over each extracted JavaScript file
  unless you pass `--no-prettier`.

## Usage

```sh
python3 unbun.py <binary> bundles/
```

By default the script writes prettified `.js` files into the target directory.

Useful flags:

- `--no-prettier` – skip the formatting step.
- `--prettier-bin /custom/path/to/prettier` – point at an alternate Prettier
  executable.

## How it works

Recent Bun builds append a trailer (`\n---- Bun! ----\n`) plus a table of file
metadata to the executable. `unbun` locates that footer, parses the table of
`CompiledModuleGraphFile` records, and writes any JavaScript-like entries to
disk with stable, sanitised filenames.

If you only need the raw bundle bytes you can comment out the Prettier step or
run the script with `--no-prettier`.

## License

MIT
