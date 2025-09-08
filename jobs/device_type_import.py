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
from nautobot.dcim.models import Manufacturer, DeviceType
from django.core.files.base import File
from django.contrib.contenttypes.models import ContentType
from nautobot.extras.models import ImageAttachment

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
    include_images = BooleanVar(
        description="Also import elevation images (front/rear) if available",
        default=False,
        required=False,
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

                # Optionally attach elevation images if available
                if data.get("include_images"):
                    try:
                        self._attach_elevation_images(device_type, manufacturer.name, device_data["model"], commit)
                    except Exception as img_err:
                        self.log_warning(f"Images not attached for {manufacturer.name} {device_data['model']}: {img_err}")
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

    # ------------------------------
    # Image handling helpers
    # ------------------------------
    def _attach_elevation_images(self, device_type, manufacturer_name, model_name, commit):
        """Attach front/rear elevation images to the given DeviceType if present on disk.

        Looks under elevation-images/<Manufacturer>/ for files named like
        "<manufacturer>-<model>.front.(png|jpg|jpeg)" (case-insensitive), and similar for rear.
        Falls back to using only the model in the filename if needed.
        """
        images_dir = os.path.join(BASE_DIR, "elevation-images", manufacturer_name)
        if not os.path.isdir(images_dir):
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
        candidate_stems = [f"{manufacturer_slug}-{model_slug}", model_slug]
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
