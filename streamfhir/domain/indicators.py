"""Indicator catalogue for a citizen stream assessment.

Representative schema: indicator families are modelled on published citizen-science
stream protocols (visual 1-10 habitat scoring as in the USDA NRCS Stream Visual
Assessment Protocol, plus simple test-strip / transparency-tube readings). It is NOT
the official OneAquaHealth app schema.

Pure data - no I/O, no FHIR.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

SCORE = "score"          # integer 1..10, higher = better condition
QUANTITY = "quantity"    # number with a UCUM unit
CATEGORY = "category"    # one of a fixed set of answers
BOOLEAN = "boolean"      # yes / no observation


@dataclass(frozen=True)
class Indicator:
    code: str
    display: str
    kind: str
    definition: str
    unit: Optional[str] = None            # UCUM code (quantities only)
    hard_range: Optional[Tuple[float, float]] = None       # outside -> error (impossible)
    plausible_range: Optional[Tuple[float, float]] = None  # outside -> warning (possible, check)
    answers: Tuple[str, ...] = ()          # allowed answers (category only)
    answer_prefix: str = ""                # prefix used for answer codes
    needs_photo_when: Tuple[object, ...] = field(default=())  # values that need photo evidence


def _score(code: str, display: str, definition: str) -> Indicator:
    return Indicator(code, display, SCORE, definition, hard_range=(1, 10))


INDICATORS: Dict[str, Indicator] = {i.code: i for i in [
    _score("channel-condition", "Channel condition score",
           "Visual score 1-10 of how natural the channel shape is (10 = natural, 1 = concrete or straightened)."),
    _score("bank-stability", "Bank stability score",
           "Visual score 1-10 of bank erosion (10 = stable vegetated banks, 1 = actively collapsing)."),
    _score("riparian-zone", "Riparian zone score",
           "Visual score 1-10 of the vegetated strip along the stream (10 = wide native vegetation, 1 = none)."),
    _score("instream-habitat", "In-stream habitat score",
           "Visual score 1-10 of places for fish and insects to live (logs, stones, pools)."),
    Indicator("water-temperature", "Water temperature", QUANTITY,
              "Water temperature measured with a thermometer.",
              unit="Cel", hard_range=(-1, 45), plausible_range=(0, 32)),
    Indicator("ph", "pH", QUANTITY, "pH measured with a test strip or pen.",
              unit="[pH]", hard_range=(0, 14), plausible_range=(5.5, 9.5)),
    Indicator("nitrate", "Nitrate (as NO3)", QUANTITY, "Nitrate measured with a colorimetric test strip.",
              unit="mg/L", hard_range=(0, 500), plausible_range=(0, 150)),
    Indicator("phosphate", "Phosphate (as PO4)", QUANTITY, "Phosphate measured with a colorimetric test kit.",
              unit="mg/L", hard_range=(0, 50), plausible_range=(0, 5)),
    Indicator("transparency", "Transparency tube depth", QUANTITY,
              "Depth at which the marker disappears in a transparency tube.",
              unit="cm", hard_range=(0, 200), plausible_range=(0, 120)),
    Indicator("water-colour", "Water colour", CATEGORY, "Dominant colour of the water.",
              answers=("clear", "brown-turbid", "green", "milky-grey", "other"), answer_prefix="colour"),
    Indicator("surface", "Surface film", CATEGORY, "What is floating on the surface.",
              answers=("none", "foam", "oily-sheen", "algal-scum"), answer_prefix="surface",
              needs_photo_when=("oily-sheen", "algal-scum")),
    Indicator("odour", "Odour", CATEGORY, "Smell of the water.",
              answers=("none", "earthy", "sewage", "chemical"), answer_prefix="odour"),
    Indicator("litter", "Litter", CATEGORY, "Amount of litter in or next to the water.",
              answers=("none", "some", "lots"), answer_prefix="litter"),
    Indicator("dead-fish", "Dead fish seen", BOOLEAN, "Dead fish seen in or near the water.",
              needs_photo_when=(True,)),
    Indicator("people-contact", "People in the water", BOOLEAN,
              "People (e.g. children paddling, swimmers) seen in contact with the water."),
    Indicator("animal-contact", "Pets or livestock in the water", BOOLEAN,
              "Dogs, livestock or other domestic animals seen in contact with the water."),
]}

SCORE_CODES = tuple(c for c, i in INDICATORS.items() if i.kind == SCORE)
PANEL_CODE = "stream-assessment"
PANEL_DISPLAY = "Citizen stream assessment panel"


def answer_code(indicator: Indicator, value: str) -> str:
    return "%s-%s" % (indicator.answer_prefix, value)
