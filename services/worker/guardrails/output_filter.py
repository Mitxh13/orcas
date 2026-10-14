import re
from typing import Any, Dict, List, Optional, Tuple

FORBIDDEN_PHRASES: List[str] = [
    "complaint resolved",
    "compensation granted",
    "claim approved",
    "relief granted",
    "refund ordered",
    "order passed",
    "disposed of"
]

# Rule-based toxicity word list (L4 – lightweight local classifier, documented choice).
# A production system would use a model-based classifier; this rule list catches
# overtly abusive, threatening, or discriminatory language that must never appear
# in an official regulatory routing determination.
TOXIC_WORDS: List[str] = [
    "kill", "murder", "rape", "terrorist", "bomb",
    "stupid", "idiot", "moron", "retard",
    "slur", "hate speech", "threat", "die",
    "racist", "sexist", "discriminat"
]

ALLOWED_ROUTES = {
    "assign_to_entity",
    "refer_to_other_regulator",
    "not_maintainable",
    "escalate_to_human"
}

ALLOWED_REGULATORS = {"SEBI", "RBI", "UNDETERMINED"}


class OutputFilter:
    """
    Enforces invariant output guardrails (Requirement L4):
    1. disposed MUST strictly be False (system never settles complaints).
    2. route MUST belong to ALLOWED_ROUTES enum.
    3. regulator MUST belong to ALLOWED_REGULATORS enum.
    4. Blocks forbidden settlement/disposal phrases.
    5. Toxicity check on basis text (rule-based word list).
    """
    @staticmethod
    def filter(determination: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        res = dict(determination)

        # 1. Enforce disposed = False
        res["disposed"] = False

        # 2. Check route enum
        if res.get("route") not in ALLOWED_ROUTES:
            return False, res, f"Route '{res.get('route')}' is not permitted by output guardrail."

        # 3. Check regulator enum
        if res.get("regulator") not in ALLOWED_REGULATORS:
            return False, res, f"Regulator '{res.get('regulator')}' is not permitted by output guardrail."

        # 4. Check forbidden phrases in basis
        basis_lower = res.get("basis", "").lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in basis_lower:
                # Sanitize output by escalating instead of allowing prohibited claim
                res["status"] = "escalated"
                res["route"] = "escalate_to_human"
                res["timeline"] = None
                res["escalation"] = {
                    "reason_code": "guardrail",
                    "reason": f"Output blocked by guardrail: prohibited disposal language '{phrase}' detected."
                }
                return False, res, f"Prohibited phrase '{phrase}' blocked; determination converted to escalation."

        # 5. Toxicity check (rule-based word list)
        for word in TOXIC_WORDS:
            if word in basis_lower:
                res["status"] = "escalated"
                res["route"] = "escalate_to_human"
                res["timeline"] = None
                res["escalation"] = {
                    "reason_code": "guardrail",
                    "reason": f"Output blocked by guardrail: toxic content detected."
                }
                return False, res, f"Toxic content detected in basis; determination converted to escalation."

        return True, res, None

