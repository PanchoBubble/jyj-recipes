from pydantic import BaseModel, ConfigDict

from jyj.schemas.common import DecimalStr
from jyj.units import Dimension


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    dimension: Dimension
    to_base: DecimalStr | None
