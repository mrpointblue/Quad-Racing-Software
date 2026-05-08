from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from backend.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)

    event_number = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    event_date = Column(String, nullable=True)
    location = Column(String, nullable=True)

    # Ortsclub / Veranstalter
    organizer_club = Column(String, nullable=True)

    sectors_per_lane = Column(Integer, default=4)

    active = Column(Boolean, default=True)

    closed = Column(Boolean, default=False)
    closed_at = Column(String, nullable=True)
    closed_by = Column(String, nullable=True)
    closed_reason = Column(String, nullable=True)

    participants = relationship("Participant", back_populates="event")
    pairings = relationship("Pairing", back_populates="event")
    registration_groups = relationship("RegistrationGroup", back_populates="event")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)

    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(String, nullable=True)

    updated_at = Column(String, nullable=True)


class DriverProfile(Base):
    __tablename__ = "driver_profiles"

    id = Column(Integer, primary_key=True, index=True)

    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    birth_year = Column(Integer, nullable=False)

    default_start_number = Column(Integer, nullable=True)

    club = Column(String, nullable=True)
    team = Column(String, nullable=True)

    adac_number = Column(String, nullable=True)
    youth_license_number = Column(String, nullable=True)

    active = Column(Boolean, default=True)

    participants = relationship("Participant", back_populates="driver_profile")


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)

    # optionale Verbindung zum Fahrerstamm
    driver_profile_id = Column(Integer, ForeignKey("driver_profiles.id"), nullable=True)

    start_number = Column(Integer, index=True, nullable=False)

    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)

    birth_year = Column(Integer, nullable=False)
    race_class = Column(Integer, nullable=False)

    club = Column(String, nullable=True)
    team = Column(String, nullable=True)

    adac_number = Column(String, nullable=True)
    youth_license_number = Column(String, nullable=True)

    # Ausscheiden während der Veranstaltung
    withdrawn = Column(Boolean, default=False)
    withdrawn_reason = Column(String, nullable=True)
    withdrawn_at = Column(String, nullable=True)
    withdrawn_by = Column(String, nullable=True)

    checked_in = Column(Boolean, default=False)
    active = Column(Boolean, default=True)

    event = relationship("Event", back_populates="participants")
    driver_profile = relationship("DriverProfile", back_populates="participants")


class Pairing(Base):
    __tablename__ = "pairings"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)

    race_class = Column(Integer, nullable=False)
    round_name = Column(String, nullable=False)
    pairing_number = Column(Integer, nullable=False)

    driver_1_id = Column(Integer, ForeignKey("participants.id"), nullable=False)
    driver_2_id = Column(Integer, ForeignKey("participants.id"), nullable=True)

    is_single_driver = Column(Boolean, default=False)

    status = Column(String, default="created")

    event = relationship("Event", back_populates="pairings")
    driver_1 = relationship("Participant", foreign_keys=[driver_1_id])
    driver_2 = relationship("Participant", foreign_keys=[driver_2_id])
    runs = relationship("RaceRun", back_populates="pairing")


class RaceRun(Base):
    __tablename__ = "race_runs"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    pairing_id = Column(Integer, ForeignKey("pairings.id"), nullable=False)

    race_class = Column(Integer, nullable=False)
    round_name = Column(String, nullable=False)
    run_number = Column(Integer, nullable=False)

    lane_a_driver_id = Column(Integer, ForeignKey("participants.id"), nullable=False)
    lane_b_driver_id = Column(Integer, ForeignKey("participants.id"), nullable=True)

    lane_a_time_ms = Column(Integer, nullable=True)
    lane_b_time_ms = Column(Integer, nullable=True)

    status = Column(String, default="created")
    confirmed = Column(Boolean, default=False)

    traffic_light_status = Column(String, default="off")
    false_start_lane = Column(String, default="none")

    started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)

    reset_count = Column(Integer, default=0)
    last_reset_at = Column(String, nullable=True)
    last_reset_by = Column(String, nullable=True)

    pairing = relationship("Pairing", back_populates="runs")
    lane_a_driver = relationship("Participant", foreign_keys=[lane_a_driver_id])
    lane_b_driver = relationship("Participant", foreign_keys=[lane_b_driver_id])
    results = relationship("RunResult", back_populates="race_run")


class RunResult(Base):
    __tablename__ = "run_results"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    race_run_id = Column(Integer, ForeignKey("race_runs.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=False)

    lane = Column(String, nullable=False)

    raw_time_ms = Column(Integer, nullable=True)

    sector_1_penalty = Column(Integer, default=0)
    sector_2_penalty = Column(Integer, default=0)
    sector_3_penalty = Column(Integer, default=0)
    sector_4_penalty = Column(Integer, default=0)
    sector_5_penalty = Column(Integer, default=0)
    sector_6_penalty = Column(Integer, default=0)
    sector_7_penalty = Column(Integer, default=0)
    sector_8_penalty = Column(Integer, default=0)
    sector_9_penalty = Column(Integer, default=0)
    sector_10_penalty = Column(Integer, default=0)

    general_penalty = Column(Integer, default=0)

    confirmed = Column(Boolean, default=False)

    race_run = relationship("RaceRun", back_populates="results")
    participant = relationship("Participant")


class RegistrationGroup(Base):
    __tablename__ = "registration_groups"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)

    group_key = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    classes = Column(String, nullable=False)

    released = Column(Boolean, default=False)
    released_at = Column(String, nullable=True)
    released_by = Column(String, nullable=True)

    release_revoked = Column(Boolean, default=False)
    release_revoked_at = Column(String, nullable=True)
    release_revoked_by = Column(String, nullable=True)
    release_revoked_reason = Column(String, nullable=True)

    event = relationship("Event", back_populates="registration_groups")


class RegistrationNotification(Base):
    __tablename__ = "registration_notifications"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)

    group_key = Column(String, index=True, nullable=False)
    group_name = Column(String, nullable=False)
    message = Column(String, nullable=False)

    created_at = Column(String, nullable=True)
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(String, nullable=True)
    acknowledged_by = Column(String, nullable=True)

    event = relationship("Event")


class MonitorDisplaySetting(Base):
    __tablename__ = "monitor_display_settings"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), unique=True, nullable=False)

    show_race_control = Column(Boolean, default=True)
    display_classes = Column(String, default="")
    display_rounds = Column(String, default="")
    show_overall_results = Column(Boolean, default=False)
    show_team_results = Column(Boolean, default=False)
    display_orientation = Column(String, default="landscape")

    updated_at = Column(String, nullable=True)

    event = relationship("Event")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String, nullable=False)

    created_at = Column(String, nullable=True)

    event = relationship("Event")
    members = relationship("TeamMember", back_populates="team")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=False)

    team = relationship("Team", back_populates="members")
    participant = relationship("Participant")
