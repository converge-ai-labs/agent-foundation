from a13n_service.database.metadata import NAMING_CONVENTION, Base, service_metadata
from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


def test_service_metadata_is_explicit_and_uses_deterministic_names() -> None:
    class MetadataProbe(Base):
        __tablename__ = "metadata_probe"
        __table_args__ = (CheckConstraint("quantity >= 0", name="quantity_nonnegative"),)

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        code: Mapped[str] = mapped_column(String(32), unique=True)
        name: Mapped[str] = mapped_column(String(32), index=True)
        quantity: Mapped[int] = mapped_column(Integer)

    try:
        assert service_metadata() is Base.metadata
        assert MetadataProbe.__table__.primary_key.name == "pk_metadata_probe"
        assert {constraint.name for constraint in MetadataProbe.__table__.constraints} >= {
            "ck_metadata_probe_quantity_nonnegative",
            "pk_metadata_probe",
            "uq_metadata_probe_code",
        }
        assert {index.name for index in MetadataProbe.__table__.indexes} == {"ix_metadata_probe_name"}
        assert NAMING_CONVENTION["fk"].startswith("fk_")
    finally:
        Base.metadata.remove(MetadataProbe.__table__)
