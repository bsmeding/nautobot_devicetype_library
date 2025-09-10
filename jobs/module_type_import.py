################################################################################
# Job to import desired module types into Nautobot from a YAML file.
#
# Created by: Bart Smeding
# Date: 2025-01-27
#
################################################################################

from nautobot.core.jobs import Job, StringVar, ChoiceVar, BooleanVar, register_jobs
import os
import yaml
import re
import shutil
from nautobot.dcim.models import (
    Manufacturer, ModuleType, ConsolePortTemplate, ConsoleServerPortTemplate,
    PowerPortTemplate, PowerOutletTemplate, FrontPortTemplate, RearPortTemplate
)
from nautobot.extras.models import ImageAttachment
from django.core.files.base import File
from django.core.files.images import ImageFile
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.conf import settings

# Set the relative path to the module types folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Navigate up from jobs/
MODULE_TYPE_PATH = os.path.join(BASE_DIR, "module-types")
MODULE_IMAGE_PATH = os.path.join(BASE_DIR, "module-images")


class SyncModuleTypes(Job):
    text_filter = StringVar(
        description="Enter text to filter module types (regex supported)",
        required=False
    )
    manufacturer = ChoiceVar(
        choices=[], 
        description="Select a manufacturer to import all module types",
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
        description="Include module images during import",
        default=True
    )

    class Meta:
        name = "Import Module Types"
        description = "Import module types from YAML files"
        commit_default = False

    @classmethod
    def get_manufacturer_choices(cls):
        """Populate the manufacturer dropdown choices dynamically with an empty default value."""
        manufacturers = [("", "Select a manufacturer")]
        if os.path.exists(MODULE_TYPE_PATH):
            for folder in os.listdir(MODULE_TYPE_PATH):
                folder_path = os.path.join(MODULE_TYPE_PATH, folder)
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
        return super().as_form(*args, **kwargs)


    def run(self, *args, **kwargs):
        """Execute the job with dynamic argument handling."""
        debug_mode = kwargs.get("debug_mode", False)
        manufacturer = kwargs.get("manufacturer")
        text_filter = kwargs.get("text_filter")
        dry_run = kwargs.get("dry_run")
        include_images = kwargs.get("include_images", False)

        self.logger.info("Starting module type synchronization...")

        # Verify if the directory exists
        if not os.path.exists(MODULE_TYPE_PATH):
            self.logger.error("Module types directory not found.")
            return

        if text_filter == '' and manufacturer == '':
            self.logger.error("No filter set, to prohibit accidentally import all module_types this is stopped.")
            return

        # Walk through the directory structure and log all folders and files
        if debug_mode:
            self.logger.info(f"Scanning directory structure under {MODULE_TYPE_PATH}...")

        files_to_import = []

        for root, dirs, files in os.walk(MODULE_TYPE_PATH):
            if debug_mode:
                self.logger.info(f"Checking folder: {root}")
            for file in files:
                if file.endswith('.yaml') or file.endswith('.yml'):
                    file_path = os.path.join(root, file)
                    files_to_import.append(file_path)
                    if debug_mode:
                        self.logger.info(f"Found YAML file: {file_path}")

        if not files_to_import:
            self.logger.warning("No YAML files found in the module types directory.")
            return

        self.logger.info(f"Found {len(files_to_import)} YAML files to process.")

        # Process each YAML file
        for file_path in files_to_import:
            self.logger.info(f"Processing file: {file_path}")
            try:
                # Extract manufacturer from folder name
                folder_name = os.path.basename(os.path.dirname(file_path))
                
                with open(file_path, 'r') as f:
                    data = yaml.safe_load(f)
                
                if not isinstance(data, list):
                    self.logger.error(f"YAML file {file_path} does not contain a list of module types.")
                    continue

                for module_data in data:
                    if not isinstance(module_data, dict):
                        self.logger.error(f"Invalid module type data in {file_path}: {module_data}")
                        continue

                    # Apply filters
                    if manufacturer and folder_name.lower() != manufacturer.lower():
                        continue
                    
                    if text_filter:
                        import re
                        if not re.search(text_filter, module_data.get("model", ""), re.IGNORECASE):
                            continue

                    if dry_run:
                        self.logger.info(f"[Dry-run] Would import module type: {folder_name} {module_data.get('model', 'Unknown')}")
                        continue

                    # Create or update the module type using folder name as manufacturer
                    try:
                        module_type = self._create_or_update_module_type(module_data, folder_name)
                        self.logger.info(f"ModuleType created: {module_type.id} - {module_type.manufacturer.name} {module_type.model}")

                        # Attach images if requested
                        if include_images:
                            try:
                                # Try using part_number first, then fall back to model
                                model_for_images = module_data.get("part_number", module_data["model"])
                                # Wrap image attachment in a transaction to ensure it's committed
                                with transaction.atomic():
                                    self._attach_module_images(module_type, folder_name, model_for_images, commit=True, debug_mode=debug_mode)
                            except Exception as img_err:
                                self.logger.warning(f"Images not attached for {folder_name} {module_data['model']}: {img_err}")

                    except Exception as e:
                        self.logger.error(f"Failed to create/update module type {folder_name} {module_data.get('model', 'Unknown')}: {e}")
                        continue

            except Exception as e:
                self.logger.error(f"Failed to process file {file_path}: {e}")
                continue

        self.logger.info("Module type synchronization completed.")

    def _create_or_update_module_type(self, data, manufacturer_name):
        """Create or update a ModuleType from the given data."""
        # Get or create manufacturer using the folder name
        manufacturer, created = Manufacturer.objects.get_or_create(
            name=manufacturer_name,
            defaults={"slug": self._slugify(manufacturer_name)}
        )
        if created:
            self.logger.info(f"Created manufacturer: {manufacturer.name}")

        # Create or update module type
        module_type, created = ModuleType.objects.get_or_create(
            manufacturer=manufacturer,
            model=data["model"],
            defaults={
                "slug": self._slugify(data["model"]),
                "part_number": data.get("part_number", ""),
                "description": data.get("description", ""),
                "weight": data.get("weight"),
                "weight_unit": data.get("weight_unit", "kg"),
                "is_full_depth": data.get("is_full_depth", True),
            }
        )

        if not created:
            # Update existing module type
            module_type.part_number = data.get("part_number", "")
            module_type.description = data.get("description", "")
            module_type.weight = data.get("weight")
            module_type.weight_unit = data.get("weight_unit", "kg")
            module_type.is_full_depth = data.get("is_full_depth", True)
            module_type.save()

        return module_type

    def _slugify(self, value):
        """Convert a string to a URL-friendly slug."""
        if not value:
            return ""
        # Convert to lowercase and replace spaces/special chars with hyphens
        slug = re.sub(r'[^\w\s-]', '', value.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug.strip('-')

    
    # ------------------------------
    # Image handling helpers
    # ------------------------------
    def _copy_image_to_media(self, source_path, target_filename, debug_mode=False):
        """Copy an image file directly to the media/moduletype-images/ directory.
        
        Returns the relative path to the copied file, or None if copy failed.
        """
        try:
            # Get the media root directory
            media_root = getattr(settings, 'MEDIA_ROOT', '/opt/nautobot/media')
            target_dir = os.path.join(media_root, 'moduletype-images')
            
            # Ensure the target directory exists
            os.makedirs(target_dir, exist_ok=True)
            
            # Create the full target path
            target_path = os.path.join(target_dir, target_filename)
            
            # Check if target file already exists and has the same size
            if os.path.exists(target_path):
                try:
                    source_size = os.path.getsize(source_path)
                    target_size = os.path.getsize(target_path)
                    if source_size == target_size:
                        if debug_mode:
                            self.logger.debug(f"File already exists with same size, skipping copy: {target_filename}")
                        return f"moduletype-images/{target_filename}"
                    else:
                        if debug_mode:
                            self.logger.debug(f"File exists but size differs: {target_filename} (source={source_size}, target={target_size})")
                except OSError as size_err:
                    if debug_mode:
                        self.logger.debug(f"Could not check file sizes: {size_err}")
                    # Continue with copy attempt
            
            # Copy the file
            try:
                shutil.copy2(source_path, target_path)
            except PermissionError as perm_err:
                self.logger.error(f"Permission denied copying {target_filename}: {perm_err}")
                return None
            except OSError as os_err:
                self.logger.error(f"OS error copying {target_filename}: {os_err}")
                return None
            
            # Verify the copy was successful
            if os.path.exists(target_path):
                copied_size = os.path.getsize(target_path)
                source_size = os.path.getsize(source_path)
                if copied_size == source_size:
                    self.logger.info(f"Successfully copied image: {target_filename} ({copied_size} bytes)")
                    return f"moduletype-images/{target_filename}"
                else:
                    self.logger.error(f"File copy size mismatch: source={source_size}, copied={copied_size}")
                    return None
            else:
                self.logger.error(f"File copy failed: {target_path} does not exist")
                return None
                
        except Exception as copy_err:
            self.logger.error(f"Failed to copy image file {target_filename}: {copy_err}")
            # Check if the file actually exists despite the error
            if os.path.exists(target_path):
                self.logger.info(f"File {target_filename} exists despite error, using existing file")
                return f"moduletype-images/{target_filename}"
            return None

    def _attach_module_images(self, module_type, manufacturer_name, model_name, commit, debug_mode=False):
        """Attach front/rear module images to the given ModuleType if present on disk.

        Looks under module-images/<Manufacturer>/ for files named like
        "<manufacturer>-<model>.front.(png|jpg|jpeg)" (case-insensitive), and similar for rear.
        Falls back to using only the model in the filename if needed.
        """
        images_dir = self._resolve_manufacturer_images_dir(manufacturer_name)
        if not images_dir:
            self.logger.info(f"No module-images directory for manufacturer '{manufacturer_name}'. Skipping images.")
            return

        front_path, rear_path = self._find_module_image_paths(images_dir, manufacturer_name, model_name, debug_mode)

        if not front_path and not rear_path:
            self.logger.info(f"No module images found for {manufacturer_name} {model_name}.")
            return

        # For dry run, just log what would be attached
        if not commit:
            if front_path:
                self.logger.info(f"[Dry-run] Would attach FRONT image: {front_path}")
            if rear_path:
                self.logger.info(f"[Dry-run] Would attach REAR image: {rear_path}")
            return

        # Attach FRONT: copy file directly to media folder and create ImageAttachment
        if front_path:
            try:
                # Copy the file directly to the media folder
                filename = os.path.basename(front_path)
                relative_path = self._copy_image_to_media(front_path, filename, debug_mode)
                
                if relative_path:
                    # Create ImageAttachment for the module type
                    self._attach_with_imageattachment(module_type, front_path, name_suffix="front elevation")
                    self.logger.info(f"Successfully attached front image: {filename}")
                else:
                    self.logger.warning(f"Failed to copy front image, falling back to ImageAttachment")
                    self._attach_with_imageattachment(module_type, front_path, name_suffix="front elevation")
                    
            except Exception as img_err:
                self.logger.warning(f"Failed to attach front image: {img_err}")
                self._attach_with_imageattachment(module_type, front_path, name_suffix="front elevation")
            
            # Refresh the module type to ensure it's up to date
            module_type.refresh_from_db()

        # Attach REAR: copy file directly to media folder and create ImageAttachment
        if rear_path:
            try:
                # Copy the file directly to the media folder
                filename = os.path.basename(rear_path)
                relative_path = self._copy_image_to_media(rear_path, filename, debug_mode)
                
                if relative_path:
                    # Create ImageAttachment for the module type
                    self._attach_with_imageattachment(module_type, rear_path, name_suffix="rear elevation")
                    self.logger.info(f"Successfully attached rear image: {filename}")
                else:
                    self.logger.warning(f"Failed to copy rear image, falling back to ImageAttachment")
                    self._attach_with_imageattachment(module_type, rear_path, name_suffix="rear elevation")
                    
            except Exception as img_err:
                self.logger.warning(f"Failed to attach rear image: {img_err}")
                self._attach_with_imageattachment(module_type, rear_path, name_suffix="rear elevation")
            
            # Final refresh and verification
            module_type.refresh_from_db()
            
            # Log summary of attached images
            attached_images = []
            # Check for ImageAttachments since ModuleType doesn't have dedicated image fields
            ct = ContentType.objects.get_for_model(module_type)
            attachments = ImageAttachment.objects.filter(content_type=ct, object_id=module_type.id)
            if attachments.filter(name__icontains="front").exists():
                attached_images.append("front")
            if attachments.filter(name__icontains="rear").exists():
                attached_images.append("rear")
            
            if attached_images:
                self.logger.info(f"Successfully attached {', '.join(attached_images)} image(s) to {manufacturer_name} {model_name}")
            else:
                self.logger.warning(f"No images were attached to {manufacturer_name} {model_name}")

    def _attach_with_imageattachment(self, module_type, image_path, name_suffix):
        """Create or replace an ImageAttachment for the given object."""
        ct = ContentType.objects.get_for_model(module_type)
        # Remove existing similar-named attachments to avoid duplicates
        ImageAttachment.objects.filter(
            content_type=ct,
            object_id=module_type.id,
            name__icontains=name_suffix
        ).delete()

        # Create new attachment
        with transaction.atomic():
            with open(image_path, 'rb') as fh:
                attachment = ImageAttachment.objects.create(
                    content_type=ct,
                    object_id=module_type.id,
                    name=f"{module_type.manufacturer.name} {module_type.model} {name_suffix}",
                    image=File(fh, name=os.path.basename(image_path))
                )
            
            self.logger.info(f"Attachment created successfully:")
            self.logger.info(f"ID: {attachment.id}")
            self.logger.info(f"Name: {attachment.name}")
            self.logger.info(f"Content Type: {attachment.content_type}")
            self.logger.info(f"Object ID: {attachment.object_id}")
            self.logger.info(f"Module Type ID: {module_type.id}")
            self.logger.info(f"Stored at: {attachment.image.path}")
            self.logger.info(f"File exists: {os.path.exists(attachment.image.path)}")
            self.logger.info(f"File size: {os.path.getsize(attachment.image.path)} bytes")
            self.logger.info(f"URL: {attachment.image.url}")
            
            # Test query to verify attachment was created
            test_attachments = ImageAttachment.objects.filter(
                content_type=ct,
                object_id=module_type.id,
                name__icontains=name_suffix
            )
            self.logger.info(f"Query test: Found {test_attachments.count()} matching attachments")
            if test_attachments.exists():
                self.logger.info(f"Query test: First attachment ID: {test_attachments.first().id}")

    def _find_module_image_paths(self, images_dir, manufacturer_name, model_name, debug_mode=False):
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
        for root, dirs, files in os.walk(images_dir):
            for fname in files:
                if fname.lower().endswith(('.png', '.jpg', '.jpeg')):
                    filename_to_path[fname.lower()] = os.path.join(root, fname)

        manufacturer_slug = self._slugify(manufacturer_name)
        model_slug = self._slugify(model_name)
        
        # Debug logging
        if debug_mode and hasattr(self, 'logger'):
            self.logger.debug(f"Looking for images: manufacturer='{manufacturer_name}' -> '{manufacturer_slug}', model='{model_name}' -> '{model_slug}'")
            self.logger.debug(f"Found {len(filename_to_path)} image files in directory")

        # Generate base variants: original model slug and versions with common prefixes removed
        base_variants = []
        if model_slug:
            base_variants.append(model_slug)
            # Try without common prefixes
            for prefix in ['cisco-', 'juniper-', 'arista-', 'hp-', 'dell-']:
                if model_slug.startswith(prefix):
                    base_variants.append(model_slug[len(prefix):])
                    break

        # Generate candidate stems
        candidate_stems = []
        for variant in base_variants:
            candidate_stems.append(variant)
            if manufacturer_slug:
                candidate_stems.append(f"{manufacturer_slug}-{variant}")

        # Remove duplicates while preserving order
        candidate_stems = list(dict.fromkeys(candidate_stems))

        extensions = ["png", "jpg", "jpeg"]

        front_path = None
        rear_path = None
        
        # Debug logging for candidate stems
        if debug_mode and hasattr(self, 'logger'):
            self.logger.debug(f"Trying {len(candidate_stems)} candidate stems")
        
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

        if debug_mode and hasattr(self, 'logger'):
            self.logger.debug(f"Final result: front={front_path}, rear={rear_path}")
        
        return (front_path, rear_path)

    def _resolve_manufacturer_images_dir(self, manufacturer_name):
        """Resolve the images directory for a manufacturer in a case/slug-insensitive way."""
        if not os.path.isdir(MODULE_IMAGE_PATH):
            return None

        # Try exact match first
        exact_path = os.path.join(MODULE_IMAGE_PATH, manufacturer_name)
        if os.path.isdir(exact_path):
            return exact_path

        # Try case-insensitive match
        for item in os.listdir(MODULE_IMAGE_PATH):
            if os.path.isdir(os.path.join(MODULE_IMAGE_PATH, item)):
                if item.lower() == manufacturer_name.lower():
                    return os.path.join(MODULE_IMAGE_PATH, item)

        # Try slug-based match
        manufacturer_slug = self._slugify(manufacturer_name)
        for item in os.listdir(MODULE_IMAGE_PATH):
            if os.path.isdir(os.path.join(MODULE_IMAGE_PATH, item)):
                if self._slugify(item) == manufacturer_slug:
                    return os.path.join(MODULE_IMAGE_PATH, item)

        return None

# Register the job
register_jobs(SyncModuleTypes)
