# Real photo attribution

Both files here are real product photography from Wikimedia Commons, used as demo
fixtures by `generate_synthetic_data.py` (see `docs/decisions/0013`). Neither is CC0,
so attribution is required.

## iphone-16-back.jpg

- Source: [File:Back of iPhone 16.jpg](https://commons.wikimedia.org/wiki/File:Back_of_iPhone_16.jpg)
- Author: Kyu3a
- License: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
- Modified: downscaled to 900px on the long edge, re-encoded as JPEG quality 85 (no
  other changes)

## sony-headphones.jpg

- Source: [File:Sony Headphones (40476165073).jpg](https://commons.wikimedia.org/wiki/File:Sony_Headphones_(40476165073).jpg)
- Author: Wutthichai Charoenburi
- License: [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/)
- Modified: downscaled to 900px on the long edge, re-encoded as JPEG quality 85 (no
  other changes)
- Chosen over an earlier candidate (a Sony-WH-1000XM3 photo) that was tested and
  rejected: partial control-label text ("NC/AMBIENT") near the earcup was
  misread by Evidence Agent's OCR as a fabricated brand ("Ambienton"), producing a
  false `brandMismatch`. This photo's "SONY" wordmark is unambiguous, with no nearby
  confusable text.
