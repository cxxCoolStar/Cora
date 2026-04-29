from __future__ import annotations

from collections import Counter

from core.clawbot.schemas import UserProfileSection
from core.storage.models import UserSignalRecord


class UserProfileAggregator:
    """Build a lightweight aggregated user profile from raw signals."""

    def build(self, *, signals: list[UserSignalRecord]) -> list[UserProfileSection]:
        topic_counter = Counter()
        type_counter = Counter()

        for signal in signals:
            if signal.signal_type == "interest_topic":
                topic_counter[signal.signal_value] += 1
            elif signal.signal_type == "content_type":
                type_counter[signal.signal_value] += 1

        sections: list[UserProfileSection] = []

        top_topics = [name for name, _count in topic_counter.most_common(8)]
        if top_topics:
            sections.append(UserProfileSection(name="Recent Interest Topics", values=top_topics))

        content_types = [name for name, _count in type_counter.most_common(6)]
        if content_types:
            sections.append(UserProfileSection(name="Observed Content Types", values=content_types))

        repeated_focus = [
            f"{name} (repeated {count} times)"
            for name, count in topic_counter.most_common(3)
            if count >= 2
        ]
        if repeated_focus:
            sections.append(UserProfileSection(name="Likely Ongoing Focus", values=repeated_focus))

        return sections
