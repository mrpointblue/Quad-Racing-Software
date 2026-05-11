# Service temporarily disabled - moving to direct integration

# Setup logging
logger = logging.getLogger(__name__)


class StartDisplayService:
    """Service for ESP32 LED Matrix Start Display communication"""
    
    def __init__(self):
        self.timeout = 1.0  # 1 second timeout as required
        self.last_send_time = None
        self.last_success_time = None
        self.last_error = None
        
    def get_settings(self, db: Session) -> Dict[str, str]:
        """Get startdisplay settings from database"""
        return {
            "enabled": self._get_setting(db, "startdisplay_enabled", "false"),
            "host": self._get_setting(db, "startdisplay_host", ""),
            "port": self._get_setting(db, "startdisplay_port", "80"),
            "endpoint": self._get_setting(db, "startdisplay_endpoint", "/display"),
        }
    
    def _get_setting(self, db: Session, key: str, default: str) -> str:
        """Get a single setting value"""
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        return setting.value if setting and setting.value else default
    
    def _save_status(self, db: Session, success: bool, error: Optional[str] = None):
        """Save status of last send attempt"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        self._set_setting(db, "startdisplay_last_send", now)
        if success:
            self._set_setting(db, "startdisplay_last_success", now)
            self._set_setting(db, "startdisplay_last_error", "")
        else:
            self._set_setting(db, "startdisplay_last_error", error or "Unknown error")
        
        db.commit()
    
    def _set_setting(self, db: Session, key: str, value: str):
        """Set a setting value"""
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if setting:
            setting.value = value
        else:
            setting = SystemSetting(key=key, value=value)
            db.add(setting)
    
    def is_enabled(self, settings: Dict[str, str]) -> bool:
        """Check if startdisplay is enabled and configured"""
        return (
            settings.get("enabled", "false") == "true" and
            settings.get("host", "").strip() != ""
        )
    
    def build_url(self, settings: Dict[str, str]) -> str:
        """Build the complete URL for the ESP32"""
        host = settings.get("host", "").strip()
        port = settings.get("port", "80").strip()
        endpoint = settings.get("endpoint", "/display").strip()
        
        # Remove http:// or https:// if present
        if host.startswith("http://"):
            host = host[7:]
        elif host.startswith("https://"):
            host = host[8:]
        
        return f"http://{host}:{port}{endpoint}"
    
    def send_start_numbers(self, lane_a_number: Optional[int], lane_b_number: Optional[int]) -> bool:
        """Send start numbers to ESP32 display"""
        db = SessionLocal()
        try:
            settings = self.get_settings(db)
            
            if not self.is_enabled(settings):
                logger.debug("Startdisplay not enabled or not configured")
                return True  # Not an error, just not enabled
            
            # Build payload
            if lane_a_number is not None and lane_b_number is not None:
                payload = {
                    "mode": "start_numbers",
                    "lane_a_start_number": lane_a_number,
                    "lane_b_start_number": lane_b_number
                }
            else:
                payload = {
                    "mode": "idle",
                    "lane_a_start_number": None,
                    "lane_b_start_number": None
                }
            
            # Save last sent numbers for diagnostics
            self._set_setting(db, "startdisplay_last_lane_a", str(lane_a_number or ""))
            self._set_setting(db, "startdisplay_last_lane_b", str(lane_b_number or ""))
            
            # Send to ESP32
            url = self.build_url(settings)
            success = self._send_http_request(url, payload)
            
            # Save status
            self._save_status(db, success, None if success else self.last_error)
            
            return success
            
        except Exception as e:
            logger.error(f"Error in send_start_numbers: {e}")
            self._save_status(db, False, str(e))
            return False
        finally:
            db.close()
    
    def _send_http_request(self, url: str, payload: Dict) -> bool:
        """Send HTTP POST request to ESP32"""
        try:
            headers = {"Content-Type": "application/json"}
            data = json.dumps(payload)
            
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, data=data, headers=headers)
                
                if response.status_code == 200:
                    logger.info(f"Successfully sent to ESP32: {payload}")
                    self.last_send_time = datetime.now()
                    self.last_success_time = datetime.now()
                    self.last_error = None
                    return True
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    logger.error(f"ESP32 request failed: {error_msg}")
                    self.last_error = error_msg
                    return False
                    
        except httpx.TimeoutException:
            error_msg = f"Timeout after {self.timeout}s"
            logger.error(f"ESP32 request timeout: {error_msg}")
            self.last_error = error_msg
            return False
        except httpx.ConnectError:
            error_msg = "Connection failed"
            logger.error(f"ESP32 connection failed: {error_msg}")
            self.last_error = error_msg
            return False
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"ESP32 request error: {error_msg}")
            self.last_error = error_msg
            return False
    
    def test_connection(self) -> Dict[str, any]:
        """Test connection to ESP32 with test payload"""
        db = SessionLocal()
        try:
            settings = self.get_settings(db)
            
            if not self.is_enabled(settings):
                return {
                    "success": False,
                    "error": "Startdisplay nicht aktiviert oder nicht konfiguriert"
                }
            
            # Send test payload
            test_payload = {
                "mode": "start_numbers",
                "lane_a_start_number": 23,
                "lane_b_start_number": 54
            }
            
            url = self.build_url(settings)
            success = self._send_http_request(url, test_payload)
            
            return {
                "success": success,
                "error": None if success else self.last_error,
                "url": url
            }
            
        except Exception as e:
            logger.error(f"Error in test_connection: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            db.close()
    
    def get_status_info(self) -> Dict[str, str]:
        """Get current status information for display in settings"""
        db = SessionLocal()
        try:
            settings = self.get_settings(db)
            
            return {
                "enabled": settings.get("enabled", "false"),
                "url": self.build_url(settings) if self.is_enabled(settings) else "Nicht konfiguriert",
                "last_send": self._get_setting(db, "startdisplay_last_send", "-"),
                "last_success": self._get_setting(db, "startdisplay_last_success", "-"),
                "last_error": self._get_setting(db, "startdisplay_last_error", "-"),
                "last_lane_a": self._get_setting(db, "startdisplay_last_lane_a", "-"),
                "last_lane_b": self._get_setting(db, "startdisplay_last_lane_b", "-"),
            }
        finally:
            db.close()


# Global service instance
start_display_service = StartDisplayService()
