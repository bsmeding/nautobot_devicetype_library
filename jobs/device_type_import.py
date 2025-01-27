################################################################################
# Job to import desired device types into Nautobot from a YAML file.
#
# Created by: Bart Smeding
# Date: 2025-01-27
#
################################################################################

from nautobot.core.jobs import Job, StringVar, ChoiceVar, BooleanVar, register_jobs
import os
import yaml
import re
from nautobot.dcim.models import (
    Manufacturer, DeviceType, InterfaceTemplate, ConsolePortTemplate, ConsoleServerPortTemplate,
    PowerPortTemplate, PowerOutletTemplate, FrontPortTemplate, RearPortTemplate,
    ModuleBay, DeviceBay
)
from nautobot.extras.models import ImageAttachment
from django.core.files.images import ImageFile
from django.contrib.contenttypes.models import ContentType

# Set the relative path to the device types folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Navigate up from jobs/
DEVICE_TYPE_PATH = os.path.join(BASE_DIR, "device-types")
ELEVATION_IMAGE_PATH = os.path.join(BASE_DIR, "elevation-images")

class SyncDeviceTypes(Job):
    text_filter = StringVar(
        description="Enter text to filter device types (regex supported)",
        required=False
    )
    manufacturer = ChoiceVar(
        choices=[], 
        description="Select a manufacturer to import all device types",
        default="",
        required=False
    )
    dry_run = BooleanVar(
        description="Enable dry-run mode (only list files to be processed, no changes will be made)",
        default=True
    )
    debug_mode = BooleanVar(
        description="Enable debug mode for detailed logging",
        default=False
    )

    class Meta:
        name = "Sync Device Types"
        description = "Import device types from the local Nautobot Git repository with dry-run and debug options."
        job_class_name = "SyncDeviceTypes"

    def run(self, *args, **kwargs):
        """Execute the job with dynamic argument handling."""
        debug_mode = kwargs.get("debug_mode", False)
        manufacturer = kwargs.get("manufacturer")
        text_filter = kwargs.get("text_filter")
        dry_run = kwargs.get("dry_run")

        self.logger.info("Starting device type synchronization...")

        # Verify if the directory exists
        if not os.path.exists(DEVICE_TYPE_PATH):
            self.logger.error("Device types directory not found.")
            return


        if text_filter == '' and manufacturer == '':
            self.logger.error("No filter set, to prohibit excidentially import all device_types this is stopped.")
            return

        # Walk through the directory structure and log all folders and files
        if debug_mode:
            self.logger.info(f"Scanning directory structure under {DEVICE_TYPE_PATH}...")

        files_to_import = []

        for root, dirs, files in os.walk(DEVICE_TYPE_PATH):
            if debug_mode:
                self.logger.info(f"Checking folder: {root}")
            for file in files:
                if file.endswith(".yaml"):
                    full_path = os.path.join(root, file)
                    if manufacturer and manufacturer not in root:
                        continue  # Skip files not belonging to the selected manufacturer
                    if text_filter:
                        regex_pattern = re.compile(text_filter)
                        if not regex_pattern.search(file):
                            continue  # Skip files that do not match the regex filter
                    files_to_import.append(full_path)
                    self.logger.info(f"  Found file: {full_path}")

        if not files_to_import:
            self.logger.warning("No matching device type files found.")
            return

        # If dry-run is enabled, only list the files
        if dry_run:
            self.logger.info("Dry-run mode enabled. The following files would be processed:")
            for file_path in files_to_import:
                self.logger.info(f" - {file_path}")
            self.logger.info("Dry-run completed successfully.")
            return

        # Process and import device types
        for file_path in files_to_import:
            try:
                with open(file_path, "r") as f:
                    device_data = yaml.safe_load(f)

                manufacturer_obj, _ = Manufacturer.objects.get_or_create(name=device_data["manufacturer"])
                device_type, created = DeviceType.objects.update_or_create(
                    model=device_data["model"],
                    manufacturer=manufacturer_obj,
                    defaults={
                        "part_number": device_data.get("part_number", None),
                        "u_height": device_data.get("u_height", 1),
                        "is_full_depth": device_data.get("is_full_depth", True),
                        "comments": device_data.get("comments", ""),
                        "subdevice_role": device_data.get("subdevice_role", "") or "",
                    }
                )

                content_type = ContentType.objects.get_for_model(DeviceType)

                def upload_image(image_type, image_path):
                    existing_image = ImageAttachment.objects.filter(
                        content_type=content_type,
                        object_id=device_type.id,
                        name=f"{device_data['model']} {image_type.capitalize()} Image"
                    ).first()
                    if existing_image:
                        self.logger.info(f"{image_type.capitalize()} image already exists for {device_data['model']}, skipping upload.")
                        return
                    if os.path.exists(image_path):
                        with open(image_path, 'rb') as img:
                            image = ImageFile(img, name=os.path.basename(image_path))
                            img_attachment = ImageAttachment.objects.create(
                                content_type=content_type,
                                object_id=device_type.id,
                                image=image,
                                name=f"{device_data['model']} {image_type.capitalize()} Image"
                            )
                        setattr(device_type, f"{image_type}_image", img_attachment.image.name)
                        device_type.save()

                if device_data.get("front_image"):
                    front_image_path = os.path.join(ELEVATION_IMAGE_PATH, device_data["manufacturer"], f"{device_data['manufacturer']}-{device_data['part_number']}.front.png")
                    upload_image("front", front_image_path)

                if device_data.get("rear_image"):
                    rear_image_path = os.path.join(ELEVATION_IMAGE_PATH, device_data["manufacturer"], f"{device_data['manufacturer']}-{device_data['part_number']}.rear.png")
                    upload_image("rear", rear_image_path)

                # Helper function to process components safely
                def process_component(component_list, component_model, fields, fk_field="device_type", parent_field="device_type_id", parent_value=None):
                    """Generic function to process different device components"""
                    filter_kwargs = {fk_field: device_type}
                    component_model.objects.filter(**filter_kwargs).delete()
                    for item in device_data.get(component_list, []):
                        valid_data = {field: item.get(field, None) for field in fields if field in item}
                        if parent_value:
                            valid_data[parent_field] = parent_value
                        valid_data[fk_field] = device_type
                        component_model.objects.create(**valid_data)
                    self.logger.info(f"Checked {component_list} for {device_data['model']}.")


                # Define valid fields for each model with the correct foreign key
                process_component("interfaces", InterfaceTemplate, ["name", "type", "label", "description", "mgmt_only"])
                process_component("console-ports", ConsolePortTemplate, ["name", "type", "label", "description"])
                process_component("console-server-ports", ConsoleServerPortTemplate, ["name", "type", "label", "description"])
                process_component("power-ports", PowerPortTemplate, ["name", "type", "maximum_draw", "allocated_draw"])
                process_component("power-outlets", PowerOutletTemplate, ["name", "type", "power_port", "feed_leg", "label", "description"])
                process_component("front-ports", FrontPortTemplate, ["name", "type", "rear_port", "rear_port_position", "label", "description"])
                process_component("rear-ports", RearPortTemplate, ["name", "type", "positions", "label", "description"])
                # process_component("module-bays", ModuleBay, ["name", "position"], fk_field="name", parent_field="device_type_id", parent_value=device_type.id)
                process_component("device-bays", DeviceBay, ["name", "label", "description"], fk_field="name", parent_field="device_type", parent_value=device_type)


                self.logger.info(f"Imported device type: {device_data['model']} (Created: {created})")
            except Exception as e:
                self.logger.error(f"Failed to import {file_path}: {str(e)}")

        self.logger.info("Device type import completed successfully.")

    @classmethod
    def get_manufacturer_choices(cls):
        """Populate the manufacturer dropdown choices dynamically with an empty default value."""
        manufacturers = [("", "Select a manufacturer")]
        if os.path.exists(DEVICE_TYPE_PATH):
            for folder in os.listdir(DEVICE_TYPE_PATH):
                folder_path = os.path.join(DEVICE_TYPE_PATH, folder)
                if os.path.isdir(folder_path):
                    manufacturers.append((folder, folder))
        if len(manufacturers) == 1:
            manufacturers.append(("", "No manufacturers found"))

        return manufacturers

    @classmethod
    def as_form(cls, *args, **kwargs):
        manufacturers = cls.get_manufacturer_choices()
        if manufacturers:
            cls.manufacturer.choices = sorted(manufacturers, key=lambda x: x[1].lower())
        else:
            cls.manufacturer.choices = [("", "No manufacturers found")]

        form = super().as_form(*args, **kwargs)

        # Explicitly assign choices to the form instance
        form.fields["manufacturer"].choices = cls.manufacturer.choices

        print(f"Dropdown choices set: {cls.manufacturer.choices}")  # Debug print
        return form

# Register the job explicitly
register_jobs(SyncDeviceTypes)
