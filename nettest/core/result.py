"""Test result data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Status(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass
class TestResult:
    """A single test result."""
    name: str
    category: str  # http, tcp, udp, icmp, dns, ntp, security, performance
    status: Status
    message: str
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status in (Status.PASS, Status.WARN, Status.SKIP)


@dataclass
class TestSuite:
    """Collection of test results."""
    results: List[TestResult] = field(default_factory=list)

    def add(self, result: TestResult) -> None:
        self.results.append(result)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == Status.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == Status.FAIL)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == Status.WARN)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == Status.ERROR)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == Status.SKIP)

    def by_category(self, category: str) -> List[TestResult]:
        return [r for r in self.results if r.category == category]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)
