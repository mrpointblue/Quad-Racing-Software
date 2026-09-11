"""Persist reviewed check-in data in the driver database without committing."""
from sqlalchemy.orm import Session

from backend.models import DriverProfile, Registration


def save_registration_driver_profile(db: Session, registration: Registration) -> DriverProfile:
    profile = None
    if registration.driver_profile_id:
        profile = db.query(DriverProfile).filter(
            DriverProfile.id == registration.driver_profile_id,
            DriverProfile.active.is_(True),
        ).first()

    if profile is None:
        # Same identity rule as manual entry; also tolerate casing/outer spaces.
        candidates = db.query(DriverProfile).filter(
            DriverProfile.birth_year == registration.birth_year,
            DriverProfile.active.is_(True),
        ).order_by(DriverProfile.id).all()
        profile = next((
            candidate for candidate in candidates
            if candidate.first_name.strip().casefold() == registration.first_name.strip().casefold()
            and candidate.last_name.strip().casefold() == registration.last_name.strip().casefold()
        ), None)

    if profile is None:
        profile = DriverProfile(active=True)
        db.add(profile)

    profile.first_name = registration.first_name.strip()
    profile.last_name = registration.last_name.strip()
    profile.birth_year = registration.birth_year
    profile.default_start_number = registration.start_number
    profile.club = registration.club
    profile.adac_number = registration.adac_member_number
    profile.youth_license_number = registration.youth_pass_number
    # Team and verified youth-card metadata are not collected here: preserve them.
    db.flush()
    registration.driver_profile_id = profile.id
    return profile
