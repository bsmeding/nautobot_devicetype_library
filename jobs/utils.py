"""
Common utilities for Nautobot device type and component synchronization jobs.

This module provides shared functionality used by multiple jobs:
- Image handling (copy, slugify, find elevation images)
- Component processing (bulk operations, validation)
- YAML validation
- Logging helpers
"""

import os
import re
import shutil
from typing import Dict, List, Optional, Any, Tuple
from django.conf import settings
from django.db import transaction


class ImageHandler:
    """Handles image operations for device types and module types."""

    def __init__(self, logger=None):
        """
        Initialize ImageHandler.

        Args:
            logger: Optional logger instance for logging operations
        """
        self.logger = logger

    def copy_image_to_media(
        self,
        source_path: str,
        target_filename: str,
        target_subdir: str = "devicetype-images",
        debug_mode: bool = False
    ) -> Optional[str]:
        """
        Copy an image file to the media directory.

        Args:
            source_path: Path to the source image file
            target_filename: Name for the target file
            target_subdir: Subdirectory within MEDIA_ROOT (default: devicetype-images)
            debug_mode: Enable debug logging

        Returns:
            Relative path to copied file, or None if copy failed

        Raises:
            FileNotFoundError: If source file doesn't exist
            PermissionError: If insufficient permissions
        """
        try:
            # Sanitize target filename to prevent path traversal
            safe_filename = os.path.basename(target_filename)

            # Get media root and create target directory
            media_root = getattr(settings, 'MEDIA_ROOT', '/opt/nautobot/media')
            target_dir = os.path.join(media_root, target_subdir)
            os.makedirs(target_dir, exist_ok=True)

            # Create full target path
            target_path = os.path.join(target_dir, safe_filename)

            # Check if file already exists with same size (skip copy)
            if os.path.exists(target_path):
                try:
                    source_size = os.path.getsize(source_path)
                    target_size = os.path.getsize(target_path)
                    if source_size == target_size:
                        if debug_mode and self.logger:
                            self.logger.debug(
                                f"File already exists with same size, skipping: {safe_filename}"
                            )
                        return f"{target_subdir}/{safe_filename}"
                except OSError:
                    pass  # Continue with copy if size check fails

            # Copy the file
            shutil.copy2(source_path, target_path)

            # Verify copy was successful
            if os.path.exists(target_path):
                copied_size = os.path.getsize(target_path)
                source_size = os.path.getsize(source_path)
                if copied_size == source_size:
                    if self.logger:
                        self.logger.info(
                            f"Successfully copied image: {safe_filename} ({copied_size} bytes)"
                        )
                    return f"{target_subdir}/{safe_filename}"
                else:
                    if self.logger:
                        self.logger.error(
                            f"File size mismatch: source={source_size}, target={copied_size}"
                        )
                    return None

            if self.logger:
                self.logger.error(f"File copy failed: {target_path} doesn't exist")
            return None

        except FileNotFoundError as e:
            if self.logger:
                self.logger.error(f"Source file not found: {source_path}")
            raise

        except PermissionError as e:
            if self.logger:
                self.logger.error(f"Permission denied copying {target_filename}: {e}")
            # Check if file exists despite error
            if os.path.exists(target_path):
                if self.logger:
                    self.logger.info(f"File exists despite error, using existing: {safe_filename}")
                return f"{target_subdir}/{safe_filename}"
            raise

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to copy image {target_filename}: {e}")
            return None

    def slugify(self, value: str) -> str:
        """
        Convert a string to a URL-friendly slug.

        This matches the slugification used in devicetype-library filenames.

        Args:
            value: String to slugify

        Returns:
            Slugified string (lowercase, hyphens, alphanumeric only)

        Examples:
            >>> handler = ImageHandler()
            >>> handler.slugify("Cisco Catalyst 9200")
            'cisco-catalyst-9200'
            >>> handler.slugify("HP ProCurve 2920-48G")
            'hp-procurve-2920-48g'
        """
        value = str(value).strip().lower()
        # Replace whitespace and underscores with hyphens
        value = re.sub(r"[\s_]+", "-", value)
        # Remove non-alphanumeric characters except hyphens
        value = re.sub(r"[^a-z0-9-]", "", value)
        # Collapse multiple hyphens
        value = re.sub(r"-+", "-", value)
        # Remove leading/trailing hyphens
        value = value.strip("-")
        return value

    def find_elevation_image_paths(
        self,
        images_dir: str,
        manufacturer_name: str,
        model_name: str,
        debug_mode: bool = False
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Find front and rear elevation images for a device/module.

        Searches for image files matching patterns:
        - <manufacturer>-<model>.front.<ext>
        - <manufacturer>-<model>.rear.<ext>
        - <model>.front.<ext>
        - <model>.rear.<ext>

        All matching is case-insensitive. Tries progressive stem matching
        (e.g., "c9200-48p-4g" → "c9200-48p" → "c9200").

        Args:
            images_dir: Directory containing images
            manufacturer_name: Manufacturer name
            model_name: Model name
            debug_mode: Enable debug logging

        Returns:
            Tuple of (front_path, rear_path), either can be None
        """
        if not os.path.isdir(images_dir):
            return (None, None)

        # Build lowercase filename lookup
        filename_to_path = {}
        for root, _, files in os.walk(images_dir):
            for fname in files:
                filename_to_path[fname.lower()] = os.path.join(root, fname)

        manufacturer_slug = self.slugify(manufacturer_name)
        model_slug = self.slugify(model_name)

        if debug_mode and self.logger:
            self.logger.debug(
                f"Looking for images: mfg='{manufacturer_name}' → '{manufacturer_slug}', "
                f"model='{model_name}' → '{model_slug}'"
            )
            self.logger.debug(f"Found {len(filename_to_path)} files in {images_dir}")

        # Generate progressive stems (e.g., "c9200-48p-4g" → "c9200-48p" → "c9200")
        candidate_stems = self._generate_candidate_stems(model_slug, manufacturer_slug)

        # Try to find front and rear images
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

        if debug_mode and self.logger:
            self.logger.debug(f"Result: front={front_path}, rear={rear_path}")

        return (front_path, rear_path)

    def _generate_candidate_stems(
        self,
        model_slug: str,
        manufacturer_slug: str
    ) -> List[str]:
        """
        Generate candidate filename stems for image matching.

        Tries multiple variations:
        - Original model slug
        - Model slug with common prefixes removed (e.g., "catalyst-")
        - Model slug with 'c' prefix added (e.g., "9300" → "c9300")
        - Progressive truncation (e.g., "c9300-48p-4g" → "c9300-48p" → "c9300")
        - All variations with manufacturer prefix

        Args:
            model_slug: Slugified model name
            manufacturer_slug: Slugified manufacturer name

        Returns:
            List of candidate stems to try (ordered by priority)
        """
        base_variants = []

        if model_slug:
            base_variants.append(model_slug)

            # Remove common marketing prefixes
            common_prefixes = ["catalyst-", "procurve-", "powerconnect-"]
            for prefix in common_prefixes:
                if model_slug.startswith(prefix):
                    base_variants.append(model_slug[len(prefix):])

        # Add 'c' prefix variants (common for Cisco)
        augmented_variants = list(base_variants)
        for variant in base_variants:
            parts = variant.split("-") if variant else []
            if parts and not parts[0].startswith("c") and parts[0].isdigit():
                prefixed = "-".join(["c" + parts[0]] + parts[1:])
                if prefixed not in augmented_variants:
                    augmented_variants.append(prefixed)

        # Generate progressive stems (truncate from right)
        progressive_stems = []
        for variant in augmented_variants:
            current = variant
            while current:
                if current not in progressive_stems:
                    progressive_stems.append(current)
                if "-" not in current:
                    break
                current = current.rsplit("-", 1)[0]

        # Combine with manufacturer-prefixed variants
        candidate_stems = []
        for stem in progressive_stems:
            candidate_stems.append(stem)
            if manufacturer_slug:
                candidate_stems.append(f"{manufacturer_slug}-{stem}")

        return candidate_stems

    def resolve_manufacturer_images_dir(
        self,
        base_images_dir: str,
        manufacturer_name: str
    ) -> Optional[str]:
        """
        Find the images directory for a manufacturer (case-insensitive).

        Args:
            base_images_dir: Root images directory (e.g., elevation-images/)
            manufacturer_name: Manufacturer name to search for

        Returns:
            Full path to manufacturer's image directory, or None if not found
        """
        if not os.path.isdir(base_images_dir):
            return None

        target_lower = str(manufacturer_name).lower()
        target_slug = self.slugify(manufacturer_name)

        try:
            for entry in os.listdir(base_images_dir):
                full_path = os.path.join(base_images_dir, entry)
                if not os.path.isdir(full_path):
                    continue

                # Try case-insensitive exact match
                if entry.lower() == target_lower:
                    return full_path

                # Try slug match
                if self.slugify(entry) == target_slug:
                    return full_path

        except Exception:
            return None

        return None


class ComponentProcessor:
    """Handles bulk component operations for device types and modules."""

    def __init__(self, logger=None):
        """
        Initialize ComponentProcessor.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger

    def process_component_bulk(
        self,
        parent_object: Any,
        component_list_name: str,
        component_data: List[Dict[str, Any]],
        component_model: Any,
        fields: List[str],
        fk_field: str,
        defaults: Optional[Dict[str, Any]] = None,
        batch_size: int = 100
    ) -> int:
        """
        Process components in bulk (delete existing + bulk create new).

        This is more efficient than creating components one-by-one.

        Args:
            parent_object: Parent object (DeviceType, ModuleType, or Device)
            component_list_name: Name of component list in YAML (e.g., "interfaces")
            component_data: List of component dictionaries from YAML
            component_model: Django model class for components
            fields: List of valid field names for this component type
            fk_field: Foreign key field name (e.g., "device_type")
            defaults: Default values for missing fields
            batch_size: Number of objects to create per batch

        Returns:
            Number of components created

        Example:
            >>> processor = ComponentProcessor(logger)
            >>> processor.process_component_bulk(
            ...     device_type,
            ...     "interfaces",
            ...     yaml_data.get("interfaces", []),
            ...     InterfaceTemplate,
            ...     ["name", "type", "mgmt_only"],
            ...     "device_type"
            ... )
        """
        if defaults is None:
            defaults = {}

        # Delete existing components
        filter_kwargs = {fk_field: parent_object}
        component_model.objects.filter(**filter_kwargs).delete()

        if not component_data:
            if self.logger:
                self.logger.info(f"No {component_list_name} to create")
            return 0

        # Build component objects
        components_to_create = []
        for item in component_data:
            # Extract valid fields
            valid_data = {
                field: item.get(field)
                for field in fields
                if field in item
            }

            # Apply defaults
            for field, default_value in defaults.items():
                if field not in valid_data or valid_data[field] is None:
                    valid_data[field] = default_value

            # Set foreign key
            valid_data[fk_field] = parent_object

            # Filter to allowed fields only
            allowed_fields = set(fields + [fk_field] + list(defaults.keys()))
            filtered_data = {
                k: v for k, v in valid_data.items()
                if k in allowed_fields
            }

            components_to_create.append(component_model(**filtered_data))

        # Bulk create in batches
        created_count = 0
        for i in range(0, len(components_to_create), batch_size):
            batch = components_to_create[i:i + batch_size]
            component_model.objects.bulk_create(batch, batch_size=batch_size)
            created_count += len(batch)

        if self.logger:
            self.logger.info(
                f"Created {created_count} {component_list_name} for {parent_object}"
            )

        return created_count


class YAMLValidator:
    """Validates YAML data against JSON schemas."""

    def __init__(self, logger=None):
        """
        Initialize YAMLValidator.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger

    def validate_device_type(self, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate device type YAML data.

        Args:
            data: Parsed YAML data

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Basic validation (required fields)
        required_fields = ["manufacturer", "model"]

        for field in required_fields:
            if field not in data:
                error_msg = f"Missing required field: {field}"
                if self.logger:
                    self.logger.error(error_msg)
                return (False, error_msg)

        # Validate types
        if not isinstance(data.get("manufacturer"), str):
            return (False, "manufacturer must be a string")

        if not isinstance(data.get("model"), str):
            return (False, "model must be a string")

        # Validate u_height if present
        if "u_height" in data:
            try:
                u_height = float(data["u_height"])
                if u_height <= 0:
                    return (False, "u_height must be positive")
                # Check if multiple of 0.5
                if (u_height * 2) % 1 != 0:
                    return (False, "u_height must be a multiple of 0.5")
            except (ValueError, TypeError):
                return (False, "u_height must be a number")

        return (True, None)


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal attacks.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename (basename only, no directory components)
    """
    return os.path.basename(filename)
