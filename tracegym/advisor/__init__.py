"""Efficiency advisor: monitor cost/speed and propose validated cheaper configs."""

from tracegym.advisor.profile import build_profile, profile_json
from tracegym.advisor.recommend import advise, store_recommendations
from tracegym.advisor.rules import Recommendation

__all__ = ["build_profile", "profile_json", "advise", "store_recommendations", "Recommendation"]
