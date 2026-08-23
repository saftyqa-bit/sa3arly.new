from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(slots=True)
class RobotsRule:
    pattern: str
    allow: bool


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    escaped = re.escape(body).replace(r"\*", ".*")
    return re.compile("^" + escaped + ("$" if anchored else ""))


def _parse_groups(text: str) -> list[tuple[list[str], list[RobotsRule]]]:
    groups: list[tuple[list[str], list[RobotsRule]]] = []
    agents: list[str] = []
    rules: list[RobotsRule] = []
    rule_seen = False

    def flush() -> None:
        if agents:
            groups.append((list(agents), list(rules)))

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if rule_seen:
                flush()
                agents = []
                rules = []
                rule_seen = False
            agents.append(value)
        elif field in {"allow", "disallow"} and agents:
            rule_seen = True
            if value:
                rules.append(RobotsRule(pattern=value, allow=field == "allow"))
    flush()
    return groups


def _select_group(
    groups: list[tuple[list[str], list[RobotsRule]]], user_agent: str
) -> list[RobotsRule] | None:
    product_token = user_agent.strip().lower().split("/", 1)[0]
    best: tuple[int, list[RobotsRule]] | None = None
    wildcard: list[RobotsRule] | None = None
    for agents, rules in groups:
        for agent in agents:
            normalized = agent.strip().lower()
            if normalized == "*":
                if wildcard is None:
                    wildcard = rules
                continue
            if product_token == normalized or product_token.startswith(normalized):
                if best is None or len(normalized) > best[0]:
                    best = (len(normalized), rules)
    return best[1] if best is not None else wildcard


def robots_can_fetch(robots_text: str, user_agent: str, url: str) -> bool:
    """Apply longest-path robots precedence with Allow winning equal ties.

    urllib.robotparser uses first-match behavior. Real stores such as Amazon
    rely on a broad Disallow followed by a more specific Allow, so first-match
    incorrectly rejects legitimate product URLs.
    """
    rules = _select_group(_parse_groups(robots_text), user_agent)
    if not rules:
        return True

    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    best_rule: RobotsRule | None = None
    best_length = -1
    for rule in rules:
        if not _pattern_to_regex(rule.pattern).match(path):
            continue
        length = len(rule.pattern)
        if length > best_length or (
            length == best_length
            and rule.allow
            and best_rule is not None
            and not best_rule.allow
        ):
            best_length = length
            best_rule = rule
    return best_rule.allow if best_rule is not None else True
