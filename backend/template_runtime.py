from fastapi.templating import Jinja2Templates
from backend.time_utils import format_time_input_code
from backend.template_helpers import register_template_helpers


templates = Jinja2Templates(directory="backend/templates")

register_template_helpers(templates)

templates.env.globals["format_time_input_code"] = format_time_input_code
