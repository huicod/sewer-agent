"""Pinned image-review dispatch, shared by primary and optional-tool activities."""

from pipe_inspector.agent.guided_fusion import FUSION_VERSION
from pipe_inspector.agent.guided_policy import decide_image_guided
from pipe_inspector.agent.guided_review import (
    GUIDED_PROMPT_VERSION,
    GUIDED_PROTECTION_POLICY_VERSION,
    GuidedReview,
)
from pipe_inspector.agent.guided_review import VIEW_POLICY_VERSION as GUIDED_VIEWS
from pipe_inspector.agent.image_discovery import DISCOVERY_PROMPT_VERSION
from pipe_inspector.agent.image_discovery import VIEW_POLICY_VERSION as DISCOVERY_VIEWS
from pipe_inspector.agent.image_review import IMAGE_REVIEW_PROMPT_VERSION
from pipe_inspector.agent.recall_policy import RECALL_POLICY_VERSION, decide_image_recall
from pipe_inspector.contracts.inspection import ImageReview


def review_pins(mode: str) -> dict:
    policies = {
        "guided_review": (
            FUSION_VERSION,
            GUIDED_PROMPT_VERSION,
            GUIDED_VIEWS,
            GUIDED_PROTECTION_POLICY_VERSION,
        ),
        "candidate_review": (RECALL_POLICY_VERSION, IMAGE_REVIEW_PROMPT_VERSION, None),
        "independent_discovery": (RECALL_POLICY_VERSION, DISCOVERY_PROMPT_VERSION, DISCOVERY_VIEWS),
    }
    if mode not in policies:
        raise ValueError("unsupported image review mode")
    values = policies[mode]
    policy, prompt, views = values[:3]
    pins = dict(
        image_review_mode=mode,
        policy_version=policy,
        prompt_version=prompt,
        view_policy_version=views,
    )
    if mode == "guided_review":
        pins["protection_policy_version"] = values[3]
    return pins


def parse_image_review(raw: dict, *, mode: str | None = None) -> ImageReview:
    # Linked tools can consume an older parent's frozen review, regardless of
    # their own admission default. Preserve nested evidence, never ignore extras.
    guided = mode == "guided_review" or any(
        "evidence_strength" in item
        for key in ("candidates", "additional_anomalies")
        for item in raw.get(key, ())
    )
    return (GuidedReview if guided else ImageReview).model_validate(raw)


def decide_for_run(config, task_id, asset, observation, review, review_error=None):
    if config["image_review_mode"] == "guided_review":
        if review is not None:
            review = GuidedReview.model_validate(review.model_dump())
        return decide_image_guided(task_id, asset, observation, review, review_error=review_error)
    return decide_image_recall(task_id, asset, observation, review, review_error=review_error)
