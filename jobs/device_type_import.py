################################################################################
# Job to import desired device types into Nautobot from a YAML file.
#
# Created by: Bart Smeding
# Date: 2025-01-27
#
################################################################################

from nautobot.core.jobs import Job, StringVar, ChoiceVar, register_jobs
import os
import yaml
import re
from nautobot.dcim.models import Manufacturer, DeviceType

# Set the relative path to the device types folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Navigate up from jobs/
DEVICE_TYPE_PATH = os.path.join(BASE_DIR, "device-types")

class SyncDeviceTypes(Job):
    text_filter = StringVar(
        description="Enter text to filter device types (regex supported)",
        required=False
    )
    manufacturer = ChoiceVar(
        choices=[], 
        description="Select a manufacturer to import all device types",
        default=""
    )

    class Meta:
        name = "Sync Device Types"
        description = "Import device types from the local Nautobot Git repository."
        job_class_name = "SyncDeviceTypes"

    def run(self, data, commit):
        self.log_info("Starting device type synchronization...")

        # Verify if the directory exists
        if not os.path.exists(DEVICE_TYPE_PATH):
            self.log_failure("Device types directory not found.")
            return

        # Prepare file list based on manufacturer and text filter
        files_to_import = []
        if data["manufacturer"]:
            manufacturer_path = os.path.join(DEVICE_TYPE_PATH, data["manufacturer"])
            for root, _, files in os.walk(manufacturer_path):
                for file in files:
                    if file.endswith(".yaml"):
                        files_to_import.append(os.path.join(root, file))

        if data["text_filter"]:
            regex_pattern = re.compile(data["text_filter"])
            files_to_import = [
                file for file in files_to_import if regex_pattern.search(os.path.basename(file))
            ]

        if not files_to_import:
            self.log_warning("No matching device type files found.")
            return

        # Process and import device types
        for file_path in files_to_import:
            try:
                with open(file_path, "r") as f:
                    device_data = yaml.safe_load(f)

                manufacturer, _ = Manufacturer.objects.get_or_create(name=device_data["manufacturer"])
                device_type, created = DeviceType.objects.update_or_create(
                    model=device_data["model"],
                    manufacturer=manufacturer,
                    defaults={
                        "part_number": device_data.get("part_number"),
                        "u_height": device_data.get("u_height"),
                        "is_full_depth": device_data.get("is_full_depth"),
                        "comments": device_data.get("comments"),
                    }
                )
                self.log_success(f"Imported device type: {device_data['model']} (Created: {created})")
            except Exception as e:
                self.log_failure(f"Failed to import {file_path}: {str(e)}")

        self.log_info("Device type import completed successfully.")

    @classmethod
    def get_manufacturer_choices(cls):
        """Populate the manufacturer dropdown choices dynamically."""
        if os.path.exists(DEVICE_TYPE_PATH):
            return [(folder, folder) for folder in os.listdir(DEVICE_TYPE_PATH) if os.path.isdir(os.path.join(DEVICE_TYPE_PATH, folder))]
        return []

    @classmethod
    def as_form(cls):
        """Override the form to dynamically load manufacturer choices."""
        cls.manufacturer.choices = cls.get_manufacturer_choices()
        return super().as_form()


# Register the job explicitly
register_jobs(SyncDeviceTypes)
