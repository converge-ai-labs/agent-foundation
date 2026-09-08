# a13n

Python SDK package for a13n Service.

## Status

This `0.0.x` package reserves the stable distribution and import names while the service API is being designed. It intentionally exposes no client API yet. Generated models and transports will be added only after the service contract is stable enough to support compatibility guarantees.

## Installation

```bash
uv add a13n
```

```python
import a13n

print(a13n.__version__)
```

## Development

Run the repository-wide Python checks from the repository root:

```bash
make sdk-python-check
```

## License

Licensed under the Apache License 2.0.
