from typing import Protocol
from uuid import UUID

from app.core.domain import BookingError
from app.models import Resource, ResourceRequirement


class AllocationStrategy(Protocol):
    def select(
        self,
        compatible: list[Resource],
        available: list[Resource],
        requirements: list[ResourceRequirement],
        requested: list[UUID] | None,
    ) -> list[Resource]: ...


class ExplicitAllocation:
    def select(
        self,
        compatible: list[Resource],
        available: list[Resource],
        requirements: list[ResourceRequirement],
        requested: list[UUID] | None,
    ) -> list[Resource]:
        identifiers = requested or []
        if len(set(identifiers)) != len(identifiers):
            raise BookingError("Resource assignments must be unique", 422)
        allowed = {row.id: row for row in compatible}
        if any(identifier not in allowed for identifier in identifiers):
            raise BookingError("A selected resource is inactive or incompatible", 422)
        selected = [allowed[identifier] for identifier in identifiers]
        if any(row not in available for row in selected):
            raise BookingError("A required resource is unavailable or has insufficient capacity")
        return selected


class GreedyCompatibleAllocation:
    """A replaceable baseline; does not combine capacities or optimize room assignments."""

    def select(
        self,
        compatible: list[Resource],
        available: list[Resource],
        requirements: list[ResourceRequirement],
        requested: list[UUID] | None,
    ) -> list[Resource]:
        selected = []
        for requirement in requirements:
            candidates = sorted(
                [row for row in available if row.resource_type == requirement.resource_type],
                key=lambda row: (row.capacity or 1000001, str(row.id)),
            )
            selected.extend(candidates[: requirement.required_count])
        if not requirements and compatible:
            selected = sorted(available, key=lambda row: (row.capacity or 1000001, str(row.id)))[:1]
        return selected
