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
    DeviceBay
)
from nautobot.extras.models import ImageAttachment
from django.core.files.base import File
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
    include_images = BooleanVar(
        description="Also import elevation images (front/rear) if available",
        default=False,
        required=False,
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
        include_images = kwargs.get("include_images", False)

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

        # If dry-run is enabled, only list the files and, if requested, show potential images
        if dry_run:
            self.logger.info("Dry-run mode enabled. The following files would be processed:")
            for file_path in files_to_import:
                self.logger.info(f" - {file_path}")
                if include_images:
                    try:
                        with open(file_path, "r") as f:
                            dd = yaml.safe_load(f)
                        images_dir = self._resolve_manufacturer_images_dir(dd["manufacturer"])
                        if not images_dir:
                            self.logger.info("[Dry-run] No elevation-images directory for manufacturer")
                            continue
                        front_path, rear_path = self._find_elevation_image_paths(images_dir, dd["manufacturer"], dd["model"])
                        if front_path:
                            self.logger.info(f"[Dry-run] Would attach FRONT image: {front_path}")
                        if rear_path:
                            self.logger.info(f"[Dry-run] Would attach REAR image: {rear_path}")
                        if not front_path and not rear_path:
                            self.logger.info("[Dry-run] No elevation images found")
                    except Exception as img_err:
                        self.logger.warning(f"[Dry-run] Unable to evaluate images for {file_path}: {img_err}")
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

                if include_images:
                    try:
                        self._attach_elevation_images(device_type, device_data["manufacturer"], device_data["model"], commit=True)
                    except Exception as img_err:
                        self.logger.warning(f"Images not attached for {device_data['manufacturer']} {device_data['model']}: {img_err}")

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
        form.fields["manufacturer"].choices = cls.manufacturer.choices
        return form

    
    # ------------------------------
    # Image handling helpers
    # ------------------------------
    def _attach_elevation_images(self, device_type, manufacturer_name, model_name, commit):
        """Attach front/rear elevation images to the given DeviceType if present on disk.

        Looks under elevation-images/<Manufacturer>/ for files named like
        "<manufacturer>-<model>.front.(png|jpg|jpeg)" (case-insensitive), and similar for rear.
        Falls back to using only the model in the filename if needed.
        """
        images_dir = self._resolve_manufacturer_images_dir(manufacturer_name)
        if not images_dir:
            self.log_info(f"No elevation-images directory for manufacturer '{manufacturer_name}'. Skipping images.")
            return

        front_path, rear_path = self._find_elevation_image_paths(images_dir, manufacturer_name, model_name)

        if not front_path and not rear_path:
            self.log_info(f"No elevation images found for {manufacturer_name} {model_name}.")
            return

        if not commit:
            if front_path:
                self.log_info(f"[Dry-run] Would attach FRONT image: {front_path}")
            if rear_path:
                self.log_info(f"[Dry-run] Would attach REAR image: {rear_path}")
            return

        # Prefer native DeviceType image fields if available; otherwise, use ImageAttachment
        has_front_field = hasattr(device_type, "front_image")
        has_rear_field = hasattr(device_type, "rear_image")

        # Attach FRONT
        if front_path:
            if has_front_field:
                # Replace existing image if present
                try:
                    if getattr(device_type, "front_image"):
                        device_type.front_image.delete(save=False)
                except Exception:
                    pass
                with open(front_path, "rb") as fp:
                    device_type.front_image.save(os.path.basename(front_path), File(fp), save=True)
                self.log_success(f"Attached FRONT image to {manufacturer_name} {model_name}.")
            else:
                self._attach_with_imageattachment(device_type, front_path, name_suffix="front elevation")
                self.log_success(f"Attached FRONT image (as attachment) to {manufacturer_name} {model_name}.")

        # Attach REAR
        if rear_path:
            if has_rear_field:
                try:
                    if getattr(device_type, "rear_image"):
                        device_type.rear_image.delete(save=False)
                except Exception:
                    pass
                with open(rear_path, "rb") as rp:
                    device_type.rear_image.save(os.path.basename(rear_path), File(rp), save=True)
                self.log_success(f"Attached REAR image to {manufacturer_name} {model_name}.")
            else:
                self._attach_with_imageattachment(device_type, rear_path, name_suffix="rear elevation")
                self.log_success(f"Attached REAR image (as attachment) to {manufacturer_name} {model_name}.")

    def _attach_with_imageattachment(self, device_type, image_path, name_suffix):
        """Create or replace an ImageAttachment for the given object."""
        ct = ContentType.objects.get_for_model(device_type)
        # Remove existing similar-named attachments to avoid duplicates
        try:
            ImageAttachment.objects.filter(content_type=ct, object_id=device_type.id, name__icontains=name_suffix).delete()
        except Exception:
            pass
        with open(image_path, "rb") as fh:
            ImageAttachment.objects.create(
                content_type=ct,
                object_id=device_type.id,
                name=f"{device_type.manufacturer.name} {device_type.model} {name_suffix}",
                image=File(fh, name=os.path.basename(image_path)),
            )

    def _find_elevation_image_paths(self, images_dir, manufacturer_name, model_name):
        """Return (front_path, rear_path) for the given manufacturer/model if found.

        Matches filenames in a case-insensitive way by normalizing to lowercase.
        Candidate stems tried:
          - <slug(manufacturer)>-<slug(model)>
          - <slug(model)>
        """
        if not os.path.isdir(images_dir):
            return (None, None)

        # Build a lookup of lowercase filename -> real path
        filename_to_path = {}
        for root, _, files in os.walk(images_dir):
            for fname in files:
                filename_to_path[fname.lower()] = os.path.join(root, fname)

        manufacturer_slug = self._slugify(manufacturer_name)
        model_slug = self._slugify(model_name)
        family_slug = model_slug.split("-")[0] if model_slug else None
        candidate_stems = [
            f"{manufacturer_slug}-{model_slug}",
            model_slug,
            f"{manufacturer_slug}-{family_slug}" if family_slug else None,
            family_slug,
        ]
        candidate_stems = [s for s in candidate_stems if s]
        extensions = ["png", "jpg", "jpeg"]

        front_path = None
        rear_path = None
        for stem in candidate_stems:
            if not front_path:
                for ext in extensions:
                    key = f"{stem}.front.{ext}"
                    if key in filename_to_path:
                        front_path = filename_to_path[key]
                        break
            if not rear_path:
                for ext in extensions:
                    key = f"{stem}.rear.{ext}"
                    if key in filename_to_path:
                        rear_path = filename_to_path[key]
                        break
            if front_path and rear_path:
                break

        return (front_path, rear_path)

    def _resolve_manufacturer_images_dir(self, manufacturer_name):
        """Resolve the images directory for a manufacturer in a case/slug-insensitive way."""
        images_root = os.path.join(BASE_DIR, "elevation-images")
        if not os.path.isdir(images_root):
            return None
        target_lower = str(manufacturer_name).lower()
        target_slug = self._slugify(manufacturer_name)
        try:
            for entry in os.listdir(images_root):
                full_path = os.path.join(images_root, entry)
                if not os.path.isdir(full_path):
                    continue
                if entry.lower() == target_lower or self._slugify(entry) == target_slug:
                    return full_path
        except Exception:
            return None
        return None

    def _slugify(self, value):
        """Simplistic slugify to align with devicetype-library filenames."""
        value = str(value).strip().lower()
        # Replace whitespace and underscores with hyphens
        value = re.sub(r"[\s_]+", "-", value)
        # Remove any character that's not alphanumeric or hyphen
        value = re.sub(r"[^a-z0-9-]", "", value)
        # Collapse multiple hyphens
        value = re.sub(r"-+", "-", value)
        return value

# Register the job explicitly
register_jobs(SyncDeviceTypes)
