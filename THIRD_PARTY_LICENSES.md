# Third-Party Licenses

This project relies on the following open-source software:

## Runtime Dependencies

| Package | License | Version | Links |
|---------|---------|---------|-------|
| **FastAPI** | MIT | ≥ 0.100.0 | [Docs](https://fastapi.tiangolo.com/) · [Repo](https://github.com/tiangolo/fastapi) |
| **Uvicorn** | BSD-3-Clause | ≥ 0.22.0 | [Docs](https://www.uvicorn.org/) · [Repo](https://github.com/encode/uvicorn) |
| **HTTPX** | BSD-3-Clause | ≥ 0.24.1 | [Docs](https://www.python-httpx.org/) · [Repo](https://github.com/encode/httpx) |
| **Pydantic** | MIT | ≥ 2.0.0 | [Docs](https://docs.pydantic.dev/) · [Repo](https://github.com/pydantic/pydantic) |
| **pydantic-settings** | MIT | ≥ 2.0.0 | [Docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) · [Repo](https://github.com/pydantic/pydantic-settings) |
| **python-dotenv** | BSD-3-Clause | ≥ 1.0.0 | [Docs](https://saurabh-kumar.com/python-dotenv/) · [Repo](https://github.com/theskumar/python-dotenv) |
| **Mutagen** | GPL-2.0-or-later | ≥ 1.46.0 | [Docs](https://mutagen.readthedocs.io/) · [Repo](https://github.com/quodlibet/mutagen) |

## Architectural References

The following open-source projects were studied for **architectural understanding only** (protocol structure, endpoint contracts, quality tier definitions). No source code was copied from any GPL-licensed project.

| Project | License | Use |
|---------|---------|-----|
| **bitchord-selfhosted-addon** (rairulyle) | MIT | Reference for the BitChord addon HTTP contract (`/manifest.json`, `/search`, `/stream`) |
| **qobuz-worker-backend** (Clash-Projects) | MIT | Reference for Qobuz API integration patterns and account pooling architecture |

## GPL Notice

**Mutagen** is licensed under GPL-2.0-or-later. It is used as an unmodified, dynamically-linked library for FLAC metadata inspection. This project does not modify or redistribute Mutagen's source code.

This project is an independent implementation of the BitChord addon protocol and lossless resolution flow. No GPL-licensed code from LastWave-Native or Meld has been copied, adapted, or derived. Architectural decisions were informed by publicly available documentation, protocol inspection, and the MIT-licensed reference projects listed above.
