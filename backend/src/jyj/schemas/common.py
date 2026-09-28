from decimal import Decimal
from typing import Annotated

from pydantic import Field, PlainSerializer

# Amounts go over the wire as strings so JSON clients never round them through floats.
DecimalStr = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]

AmountIn = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=18, decimal_places=6)]
