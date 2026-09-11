"""Exercise the actual approval handler against an isolated SQLite database."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from backend.database import Base
from backend.models import DriverProfile, Event, Participant, Registration
from backend.services.registration_profiles import save_registration_driver_profile


class RegistrationProfileTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.event = Event(event_number="TEST", name="Test")
        self.db.add(self.event)
        self.db.flush()
        self.registration = Registration(
            event_id=self.event.id, first_name="Erika", last_name="Test",
            birth_date="2000-01-01", birth_year=2000, age_at_event=26,
            class_id=4, start_number=12, club="MSC",
            adac_member_number="ADAC123", youth_pass_number="PASS123",
            registration_status="under_review",
        )
        self.db.add(self.registration)
        self.db.commit()
        self.templates = SimpleNamespace(TemplateResponse=Mock(return_value="error"))
        # Import only the handler, not main.py's startup migrations/hardware threads.
        module = ast.parse(Path("backend/main.py").read_text())
        handler = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                       and n.name == "ui_event_registration_approve")
        handler.decorator_list = []
        namespace = dict(
            Session=Session, Request=Request, Depends=Depends, get_db=lambda: None,
            Participant=Participant, IntegrityError=IntegrityError,
            RedirectResponse=RedirectResponse, templates=self.templates,
            require_event=lambda *args: self.event,
            require_registration=lambda *args: self.registration,
            get_event_year=lambda event: 2026, now_string=lambda: "2026-09-11",
            save_registration_driver_profile=save_registration_driver_profile,
        )
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "approval", "exec"), namespace)
        self.approve = namespace["ui_event_registration_approve"]

    def run_approval(self):
        return self.approve(self.event.id, self.registration.id, None, self.db)

    def test_new_driver_is_saved_and_linked_on_approval(self):
        self.assertEqual(self.db.query(DriverProfile).count(), 0)
        self.assertEqual(self.run_approval().status_code, 303)
        profile = self.db.query(DriverProfile).one()
        participant = self.db.query(Participant).one()
        self.assertEqual(self.registration.driver_profile_id, profile.id)
        self.assertEqual(participant.driver_profile_id, profile.id)
        self.assertEqual(self.registration.registration_status, "approved")
        self.assertEqual((profile.first_name, profile.last_name, profile.birth_year),
                         ("Erika", "Test", 2000))
        self.assertEqual((profile.default_start_number, profile.club,
                          profile.adac_number, profile.youth_license_number),
                         (12, "MSC", "ADAC123", "PASS123"))
        # A repeated confirmation must not create either a driver or participant.
        self.assertEqual(self.run_approval(), "error")
        self.assertEqual(self.db.query(DriverProfile).count(), 1)
        self.assertEqual(self.db.query(Participant).count(), 1)

    def add_profile(self, **overrides):
        values = dict(first_name=" erika ", last_name="TEST", birth_year=2000,
                      active=True, team="Team", youth_card_qr_url="https://example.test/card",
                      youth_card_checked=True, youth_card_checked_at="2026-01-01")
        values.update(overrides)
        profile = DriverProfile(**values)
        self.db.add(profile)
        self.db.commit()
        return profile

    def test_unlinked_existing_identity_is_reused_without_losing_metadata(self):
        existing = self.add_profile()
        self.run_approval()
        self.assertEqual(self.db.query(DriverProfile).count(), 1)
        self.assertEqual(self.registration.driver_profile_id, existing.id)
        self.assertEqual(existing.team, "Team")
        self.assertTrue(existing.youth_card_checked)
        self.assertEqual(existing.youth_card_qr_url, "https://example.test/card")

    def test_linked_profile_is_updated_even_after_name_correction(self):
        existing = self.add_profile(first_name="Old", last_name="Name")
        self.registration.driver_profile_id = existing.id
        self.db.commit()
        self.run_approval()
        self.assertEqual(self.db.query(DriverProfile).count(), 1)
        self.assertEqual(existing.first_name, "Erika")
        self.assertEqual(self.registration.driver_profile_id, existing.id)

    def test_different_birth_year_is_not_merged(self):
        self.add_profile(birth_year=1999)
        self.run_approval()
        self.assertEqual(self.db.query(DriverProfile).count(), 2)

    def test_failed_commit_rolls_back_profile_participant_and_approval(self):
        original_commit = self.db.commit
        self.db.commit = Mock(side_effect=IntegrityError("commit", {}, Exception("conflict")))
        self.assertEqual(self.run_approval(), "error")
        self.db.commit = original_commit
        self.assertEqual(self.db.query(DriverProfile).count(), 0)
        self.assertEqual(self.db.query(Participant).count(), 0)
        self.assertIsNone(self.registration.driver_profile_id)
        self.assertEqual(self.registration.registration_status, "under_review")
