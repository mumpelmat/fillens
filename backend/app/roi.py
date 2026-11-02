"""Normierte Vielecke ohne Selbstüberschneidungen; alte Rechtecke bleiben lesbar."""

from pydantic import Field, model_validator

from .schemas import Input


class Point(Input):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class PolygonROI(Input):
    points: list[Point] = Field(min_length=3, max_length=32)

    @model_validator(mode="after")
    def polygon(self):
        p = [(v.x, v.y) for v in self.points]
        n = len(p)
        area = (
            abs(
                sum(
                    p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1]
                    for i in range(n)
                )
            )
            / 2
        )
        if area < 0.0001 or len(set(p)) != n:
            raise ValueError(
                "Vieleck muss mindestens drei verschiedene Punkte und eine Fläche besitzen"
            )

        def cross(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        def on(a, b, c):
            return (
                abs(cross(a, b, c)) < 1e-10
                and min(a[0], b[0]) - 1e-10 <= c[0] <= max(a[0], b[0]) + 1e-10
                and min(a[1], b[1]) - 1e-10 <= c[1] <= max(a[1], b[1]) + 1e-10
            )

        for i in range(n):
            a, b = p[i], p[(i + 1) % n]
            for j in range(i + 1, n):
                if j == (i + 1) % n or (j + 1) % n == i:
                    continue
                c, d = p[j], p[(j + 1) % n]
                if (
                    cross(a, b, c) * cross(a, b, d) < 0
                    and cross(c, d, a) * cross(c, d, b) < 0
                ) or any((on(a, b, c), on(a, b, d), on(c, d, a), on(c, d, b))):
                    raise ValueError("Vieleck darf sich nicht selbst überschneiden")
        return self
