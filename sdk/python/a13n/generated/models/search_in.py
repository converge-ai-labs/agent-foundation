from enum import StrEnum


class SearchIn(StrEnum):
    INPUT = "input"
    INPUT_OUTPUT = "input_output"
    OUTPUT = "output"

    def __str__(self) -> str:
        return str(self.value)
