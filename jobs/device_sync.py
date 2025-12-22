"""
Nautobot Job to synchronize Device components with their Device Type definitions.

This job allows you to:
- Compare devices against their device type templates
- Add missing components (interfaces, ports, etc.)
- Remove extra components not in the device type
- Protect connected/configured components from deletion
- Generate detailed reports of changes

Author: Claude (Anthropic)
Date: 2025-12-22
"""

import json
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple, Any
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType

from nautobot.core.jobs import (
    Job,
    ObjectVar,
    MultiObjectVar,
    ChoiceVar,
    MultipleChoiceVar,
    BooleanVar,
    register_jobs
)
from nautobot.dcim.models import (
    Device,
    DeviceType,
    Site,
    Location,
    Interface,
    InterfaceTemplate,
    ConsolePort,
    ConsolePortTemplate,
    ConsoleServerPort,
    ConsoleServerPortTemplate,
    PowerPort,
    PowerPortTemplate,
    PowerOutlet,
    PowerOutletTemplate,
    FrontPort,
    FrontPortTemplate,
    RearPort,
    RearPortTemplate,
    DeviceBay,
    Cable
)
from nautobot.extras.models import Tag, Status
from nautobot.ipam.models import IPAddress, VLAN


# Component type registry: maps component names to (template_model, instance_model, fields)
COMPONENT_TYPES = {
    "interfaces": {
        "template_model": InterfaceTemplate,
        "instance_model": Interface,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description", "mgmt_only"],
        "protected_check": "check_interface_protected"
    },
    "console_ports": {
        "template_model": ConsolePortTemplate,
        "instance_model": ConsolePort,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description"],
        "protected_check": "check_port_protected"
    },
    "console_server_ports": {
        "template_model": ConsoleServerPortTemplate,
        "instance_model": ConsoleServerPort,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description"],
        "protected_check": "check_port_protected"
    },
    "power_ports": {
        "template_model": PowerPortTemplate,
        "instance_model": PowerPort,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description", "maximum_draw", "allocated_draw"],
        "protected_check": "check_port_protected"
    },
    "power_outlets": {
        "template_model": PowerOutletTemplate,
        "instance_model": PowerOutlet,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description", "feed_leg"],
        "protected_check": "check_port_protected"
    },
    "front_ports": {
        "template_model": FrontPortTemplate,
        "instance_model": FrontPort,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description", "rear_port_position"],
        "protected_check": "check_port_protected"
    },
    "rear_ports": {
        "template_model": RearPortTemplate,
        "instance_model": RearPort,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "type", "label", "description", "positions"],
        "protected_check": "check_port_protected"
    },
    "device_bays": {
        "template_model": DeviceBay,
        "instance_model": DeviceBay,
        "fk_field": "device",
        "template_fk": "device_type",
        "fields": ["name", "label", "description"],
        "protected_check": "check_device_bay_protected"
    }
}


class ComponentDiff:
    """Represents the differences between a Device and its DeviceType."""

    def __init__(self, device: Device):
        """
        Initialize ComponentDiff for a device.

        Args:
            device: Device to analyze
        """
        self.device = device
        self.device_type = device.device_type
        self.to_add: Dict[str, List] = {}      # {component_type: [template_objects]}
        self.to_remove: Dict[str, List] = {}   # {component_type: [instance_objects]}
        self.protected: Dict[str, List] = {}   # {component_type: [instance_objects]}
        self.unchanged: Dict[str, List] = {}   # {component_type: [instance_objects]}

    def __str__(self) -> str:
        """String representation of the diff."""
        parts = [f"ComponentDiff for {self.device.name}:"]
        for comp_type in self.to_add:
            if self.to_add[comp_type]:
                parts.append(f"  + {comp_type}: {len(self.to_add[comp_type])} to add")
        for comp_type in self.to_remove:
            if self.to_remove[comp_type]:
                parts.append(f"  - {comp_type}: {len(self.to_remove[comp_type])} to remove")
        for comp_type in self.protected:
            if self.protected[comp_type]:
                parts.append(f"  ⚠ {comp_type}: {len(self.protected[comp_type])} protected")
        return "\n".join(parts)

    def has_changes(self) -> bool:
        """Check if there are any changes to apply."""
        return bool(self.to_add) or bool(self.to_remove)


class ComponentSyncer:
    """Handles synchronization of a specific component type."""

    def __init__(self, component_type: str, config: Dict[str, Any], logger=None):
        """
        Initialize ComponentSyncer.

        Args:
            component_type: Type of component (e.g., "interfaces")
            config: Configuration from COMPONENT_TYPES
            logger: Optional logger instance
        """
        self.component_type = component_type
        self.template_model = config["template_model"]
        self.instance_model = config["instance_model"]
        self.fk_field = config["fk_field"]
        self.template_fk = config["template_fk"]
        self.fields = config["fields"]
        self.protected_check = config["protected_check"]
        self.logger = logger

    def compute_diff(
        self,
        device: Device,
        protect_connected: bool = True,
        protect_configured: bool = True
    ) -> Tuple[List, List, List, List]:
        """
        Compute differences between device and device type for this component.

        Args:
            device: Device to analyze
            protect_connected: Protect components with cables
            protect_configured: Protect configured components

        Returns:
            Tuple of (to_add, to_remove, protected, unchanged)
        """
        # Get templates from device type
        templates = self.template_model.objects.filter(
            **{self.template_fk: device.device_type}
        )
        template_names = {t.name: t for t in templates}

        # Get existing instances on device
        instances = self.instance_model.objects.filter(
            **{self.fk_field: device}
        )
        instance_names = {i.name: i for i in instances}

        # Compute differences
        to_add_names = set(template_names.keys()) - set(instance_names.keys())
        to_remove_names = set(instance_names.keys()) - set(template_names.keys())
        unchanged_names = set(template_names.keys()) & set(instance_names.keys())

        to_add = [template_names[name] for name in to_add_names]
        to_remove_candidates = [instance_names[name] for name in to_remove_names]
        unchanged = [instance_names[name] for name in unchanged_names]

        # Check protection for components to remove
        to_remove = []
        protected = []

        for instance in to_remove_candidates:
            is_protected = False

            if protect_connected and self._is_connected(instance):
                is_protected = True

            if protect_configured and self._is_configured(instance):
                is_protected = True

            if is_protected:
                protected.append(instance)
            else:
                to_remove.append(instance)

        return (to_add, to_remove, protected, unchanged)

    def _is_connected(self, component: Any) -> bool:
        """
        Check if a component has cables connected.

        Args:
            component: Component instance to check

        Returns:
            True if connected, False otherwise
        """
        # For Nautobot 2.x, check the cable field
        if hasattr(component, 'cable') and component.cable is not None:
            return True

        # For interfaces, also check if it's a termination point for a cable
        if isinstance(component, Interface):
            # Check if this interface is a cable termination
            return Cable.objects.filter(
                Q(_termination_a_device=component.device, _termination_a_name=component.name) |
                Q(_termination_b_device=component.device, _termination_b_name=component.name)
            ).exists()

        return False

    def _is_configured(self, component: Any) -> bool:
        """
        Check if a component has important configuration.

        Args:
            component: Component instance to check

        Returns:
            True if configured, False otherwise
        """
        # Interface-specific checks
        if isinstance(component, Interface):
            # Has IP addresses
            if component.ip_addresses.exists():
                return True

            # Has VLAN configuration
            if component.untagged_vlan or component.tagged_vlans.exists():
                return True

            # Has LAG configuration
            if hasattr(component, 'lag') and component.lag:
                return True

            # Has member interfaces (is a LAG)
            if hasattr(component, 'member_interfaces') and component.member_interfaces.exists():
                return True

        # Generic check: has non-empty description
        if hasattr(component, 'description') and component.description:
            if component.description.strip():
                return True

        return False

    def add_components(
        self,
        device: Device,
        templates: List,
        batch_size: int = 100
    ) -> int:
        """
        Add missing components to a device based on templates.

        Args:
            device: Device to add components to
            templates: List of template objects to create from
            batch_size: Number of components to create per batch

        Returns:
            Number of components created
        """
        if not templates:
            return 0

        components_to_create = []

        for template in templates:
            # Build component data from template
            component_data = {
                self.fk_field: device
            }

            # Copy fields from template
            for field in self.fields:
                if hasattr(template, field):
                    value = getattr(template, field)
                    component_data[field] = value

            # Special handling for interfaces: set status to active
            if self.instance_model == Interface:
                try:
                    active_status = Status.objects.get(name="Active")
                    component_data["status"] = active_status
                except Status.DoesNotExist:
                    pass

            components_to_create.append(self.instance_model(**component_data))

        # Bulk create in batches
        created_count = 0
        for i in range(0, len(components_to_create), batch_size):
            batch = components_to_create[i:i + batch_size]
            self.instance_model.objects.bulk_create(batch, batch_size=batch_size)
            created_count += len(batch)

        if self.logger:
            self.logger.info(
                f"Created {created_count} {self.component_type} for {device.name}"
            )

        return created_count

    def remove_components(
        self,
        device: Device,
        instances: List,
        force: bool = False
    ) -> int:
        """
        Remove extra components from a device.

        Args:
            device: Device to remove components from
            instances: List of instance objects to remove
            force: Force removal even if protected

        Returns:
            Number of components removed
        """
        if not instances:
            return 0

        removed_count = 0

        for instance in instances:
            # Double-check protection unless force is True
            if not force:
                if self._is_connected(instance) or self._is_configured(instance):
                    if self.logger:
                        self.logger.warning(
                            f"Skipping protected {self.component_type}: {instance.name}"
                        )
                    continue

            instance.delete()
            removed_count += 1

        if self.logger:
            self.logger.info(
                f"Removed {removed_count} {self.component_type} from {device.name}"
            )

        return removed_count


class SyncDeviceComponents(Job):
    """
    Synchronize device components with their device type definitions.

    This job compares devices against their device type templates and can:
    - Show differences (diff mode)
    - Add missing components (add mode)
    - Remove extra components (remove mode)
    - Full synchronization (sync mode = add + remove)
    """

    # --- DEVICE SELECTION ---
    device_type = MultiObjectVar(
        model=DeviceType,
        required=False,
        description="Synchronize all devices of these types"
    )

    site = MultiObjectVar(
        model=Site,
        required=False,
        description="Synchronize devices at these sites"
    )

    location = MultiObjectVar(
        model=Location,
        required=False,
        description="Synchronize devices at these locations"
    )

    tags = MultiObjectVar(
        model=Tag,
        required=False,
        description="Synchronize devices with these tags"
    )

    device = ObjectVar(
        model=Device,
        required=False,
        description="Synchronize a single device"
    )

    # --- SYNC MODE ---
    sync_mode = ChoiceVar(
        choices=[
            ("diff", "Diff only (show differences without changes)"),
            ("add", "Add missing components"),
            ("remove", "Remove extra components"),
            ("sync", "Full sync (add + remove)")
        ],
        default="diff",
        description="Synchronization mode"
    )

    component_types = MultipleChoiceVar(
        choices=[
            ("interfaces", "Interfaces"),
            ("console_ports", "Console Ports"),
            ("console_server_ports", "Console Server Ports"),
            ("power_ports", "Power Ports"),
            ("power_outlets", "Power Outlets"),
            ("front_ports", "Front Ports"),
            ("rear_ports", "Rear Ports"),
            ("device_bays", "Device Bays")
        ],
        default=["interfaces"],
        description="Component types to synchronize"
    )

    # --- PROTECTION OPTIONS ---
    protect_connected = BooleanVar(
        default=True,
        description="Protect components with cables connected"
    )

    protect_configured = BooleanVar(
        default=True,
        description="Protect configured components (IPs, VLANs, etc.)"
    )

    force = BooleanVar(
        default=False,
        description="⚠️ Force changes even on protected components"
    )

    # --- REPORTING ---
    export_report = BooleanVar(
        default=False,
        description="Export detailed report as JSON"
    )

    class Meta:
        name = "Sync Device Components"
        description = "Synchronize device components with their device type definition"
        field_order = [
            "device_type", "site", "location", "tags", "device",
            "sync_mode", "component_types",
            "protect_connected", "protect_configured", "force",
            "export_report"
        ]
        approval_required = False
        soft_time_limit = 1800  # 30 minutes
        time_limit = 2000

    def run(self, **kwargs):
        """Execute the synchronization job."""
        # Extract parameters
        sync_mode = kwargs.get("sync_mode", "diff")
        component_types = kwargs.get("component_types", ["interfaces"])
        protect_connected = kwargs.get("protect_connected", True)
        protect_configured = kwargs.get("protect_configured", True)
        force = kwargs.get("force", False)
        export_report = kwargs.get("export_report", False)

        # Initialize statistics
        stats = {
            "job_id": str(self.request.id) if hasattr(self, 'request') else "unknown",
            "timestamp": datetime.now().isoformat(),
            "mode": sync_mode,
            "component_types": component_types,
            "devices_processed": 0,
            "devices_succeeded": 0,
            "devices_failed": 0,
            "devices_with_changes": 0,
            "summary": defaultdict(lambda: {"added": 0, "removed": 0, "protected": 0}),
            "devices": [],
            "errors": []
        }

        self.logger.info(f"Starting device component synchronization in '{sync_mode}' mode")
        self.logger.info(f"Component types: {', '.join(component_types)}")

        # Collect devices to process
        devices = self._collect_devices(**kwargs)

        if not devices:
            self.logger.warning("No devices found matching the selection criteria")
            return

        self.logger.info(f"Found {len(devices)} device(s) to process")

        # Warn if force mode is enabled
        if force:
            self.logger.warning("⚠️ FORCE MODE ENABLED - Protected components will be modified!")

        # Process each device
        for i, device in enumerate(devices, 1):
            self.logger.info(f"Processing device {i}/{len(devices)}: {device.name}")
            stats["devices_processed"] += 1

            try:
                # Check if device has a device type
                if not device.device_type:
                    error_msg = f"Device {device.name} has no device type assigned"
                    self.logger.error(error_msg)
                    stats["errors"].append({
                        "device": device.name,
                        "error": error_msg
                    })
                    stats["devices_failed"] += 1
                    continue

                # Compute diff for this device
                diff = self._compute_device_diff(
                    device,
                    component_types,
                    protect_connected,
                    protect_configured
                )

                # Apply changes based on mode
                device_result = {
                    "device": device.name,
                    "device_type": str(device.device_type),
                    "status": "success",
                    "changes": {}
                }

                if sync_mode == "diff":
                    # Just log the differences
                    self._log_diff(device, diff)
                    device_result["changes"] = self._diff_to_dict(diff)

                else:
                    # Apply changes
                    changes = self._apply_changes(
                        device,
                        diff,
                        sync_mode,
                        force
                    )
                    device_result["changes"] = changes

                    # Update statistics
                    for comp_type, comp_changes in changes.items():
                        stats["summary"][comp_type]["added"] += comp_changes.get("added", 0)
                        stats["summary"][comp_type]["removed"] += comp_changes.get("removed", 0)
                        stats["summary"][comp_type]["protected"] += comp_changes.get("protected", 0)

                # Check if device had any changes
                if diff.has_changes():
                    stats["devices_with_changes"] += 1

                stats["devices"].append(device_result)
                stats["devices_succeeded"] += 1

            except Exception as e:
                error_msg = f"Failed to process device {device.name}: {str(e)}"
                self.logger.error(error_msg)
                stats["errors"].append({
                    "device": device.name,
                    "error": str(e)
                })
                stats["devices_failed"] += 1

        # Generate final report
        self._generate_report(stats)

        # Export JSON report if requested
        if export_report:
            self._export_json_report(stats)

        self.logger.info("Device component synchronization completed")

    def _collect_devices(self, **kwargs) -> List[Device]:
        """
        Collect devices based on selection criteria.

        Args:
            **kwargs: Job parameters

        Returns:
            List of devices to process
        """
        # If a specific device is selected, return only that one
        if kwargs.get("device"):
            return [kwargs["device"]]

        # Start with all devices
        queryset = Device.objects.all()

        # Filter by device type
        if kwargs.get("device_type"):
            queryset = queryset.filter(device_type__in=kwargs["device_type"])

        # Filter by site
        if kwargs.get("site"):
            queryset = queryset.filter(site__in=kwargs["site"])

        # Filter by location
        if kwargs.get("location"):
            queryset = queryset.filter(location__in=kwargs["location"])

        # Filter by tags
        if kwargs.get("tags"):
            for tag in kwargs["tags"]:
                queryset = queryset.filter(tags=tag)

        # Prefetch related objects for performance
        queryset = queryset.select_related("device_type", "site", "location")

        return list(queryset)

    def _compute_device_diff(
        self,
        device: Device,
        component_types: List[str],
        protect_connected: bool,
        protect_configured: bool
    ) -> ComponentDiff:
        """
        Compute differences for all component types of a device.

        Args:
            device: Device to analyze
            component_types: List of component type names to check
            protect_connected: Protect connected components
            protect_configured: Protect configured components

        Returns:
            ComponentDiff object with all differences
        """
        diff = ComponentDiff(device)

        for comp_type in component_types:
            if comp_type not in COMPONENT_TYPES:
                self.logger.warning(f"Unknown component type: {comp_type}")
                continue

            config = COMPONENT_TYPES[comp_type]
            syncer = ComponentSyncer(comp_type, config, self.logger)

            to_add, to_remove, protected, unchanged = syncer.compute_diff(
                device,
                protect_connected,
                protect_configured
            )

            if to_add:
                diff.to_add[comp_type] = to_add
            if to_remove:
                diff.to_remove[comp_type] = to_remove
            if protected:
                diff.protected[comp_type] = protected
            if unchanged:
                diff.unchanged[comp_type] = unchanged

        return diff

    def _apply_changes(
        self,
        device: Device,
        diff: ComponentDiff,
        sync_mode: str,
        force: bool
    ) -> Dict[str, Dict[str, int]]:
        """
        Apply changes to a device based on the diff and mode.

        Args:
            device: Device to modify
            diff: ComponentDiff with changes to apply
            sync_mode: Sync mode (add, remove, or sync)
            force: Force changes on protected components

        Returns:
            Dictionary of changes applied: {component_type: {added: N, removed: M}}
        """
        changes = {}

        with transaction.atomic():
            # Add components
            if sync_mode in ["add", "sync"]:
                for comp_type, templates in diff.to_add.items():
                    config = COMPONENT_TYPES[comp_type]
                    syncer = ComponentSyncer(comp_type, config, self.logger)

                    added_count = syncer.add_components(device, templates)

                    if comp_type not in changes:
                        changes[comp_type] = {}
                    changes[comp_type]["added"] = added_count

            # Remove components
            if sync_mode in ["remove", "sync"]:
                for comp_type, instances in diff.to_remove.items():
                    config = COMPONENT_TYPES[comp_type]
                    syncer = ComponentSyncer(comp_type, config, self.logger)

                    removed_count = syncer.remove_components(device, instances, force)

                    if comp_type not in changes:
                        changes[comp_type] = {}
                    changes[comp_type]["removed"] = removed_count

            # Record protected components
            for comp_type, instances in diff.protected.items():
                if comp_type not in changes:
                    changes[comp_type] = {}
                changes[comp_type]["protected"] = len(instances)

        return changes

    def _log_diff(self, device: Device, diff: ComponentDiff):
        """
        Log the differences for a device.

        Args:
            device: Device being analyzed
            diff: ComponentDiff to log
        """
        self.logger.info(f"Differences for {device.name}:")

        has_any_change = False

        for comp_type in diff.to_add:
            if diff.to_add[comp_type]:
                has_any_change = True
                self.logger.info(f"  + {comp_type}: {len(diff.to_add[comp_type])} to add")
                for template in diff.to_add[comp_type][:5]:  # Show first 5
                    self.logger.info(f"    - {template.name}")
                if len(diff.to_add[comp_type]) > 5:
                    self.logger.info(f"    ... and {len(diff.to_add[comp_type]) - 5} more")

        for comp_type in diff.to_remove:
            if diff.to_remove[comp_type]:
                has_any_change = True
                self.logger.info(f"  - {comp_type}: {len(diff.to_remove[comp_type])} to remove")
                for instance in diff.to_remove[comp_type][:5]:
                    self.logger.info(f"    - {instance.name}")
                if len(diff.to_remove[comp_type]) > 5:
                    self.logger.info(f"    ... and {len(diff.to_remove[comp_type]) - 5} more")

        for comp_type in diff.protected:
            if diff.protected[comp_type]:
                has_any_change = True
                self.logger.info(f"  ⚠ {comp_type}: {len(diff.protected[comp_type])} protected")
                for instance in diff.protected[comp_type][:5]:
                    self.logger.info(f"    - {instance.name}")
                if len(diff.protected[comp_type]) > 5:
                    self.logger.info(f"    ... and {len(diff.protected[comp_type]) - 5} more")

        if not has_any_change:
            self.logger.info("  (no changes needed)")

    def _diff_to_dict(self, diff: ComponentDiff) -> Dict:
        """
        Convert a ComponentDiff to a dictionary for reporting.

        Args:
            diff: ComponentDiff to convert

        Returns:
            Dictionary representation
        """
        result = {}

        for comp_type in diff.to_add:
            if comp_type not in result:
                result[comp_type] = {}
            result[comp_type]["to_add"] = [t.name for t in diff.to_add[comp_type]]

        for comp_type in diff.to_remove:
            if comp_type not in result:
                result[comp_type] = {}
            result[comp_type]["to_remove"] = [i.name for i in diff.to_remove[comp_type]]

        for comp_type in diff.protected:
            if comp_type not in result:
                result[comp_type] = {}
            result[comp_type]["protected"] = [i.name for i in diff.protected[comp_type]]

        return result

    def _generate_report(self, stats: Dict):
        """
        Generate and log the final report.

        Args:
            stats: Statistics dictionary
        """
        self.logger.info("=" * 60)
        self.logger.info("DEVICE COMPONENT SYNCHRONIZATION REPORT")
        self.logger.info("=" * 60)
        self.logger.info(f"Mode: {stats['mode']}")
        self.logger.info(f"Devices processed: {stats['devices_processed']}")
        self.logger.info(f"Devices succeeded: {stats['devices_succeeded']}")
        self.logger.info(f"Devices failed: {stats['devices_failed']}")
        self.logger.info(f"Devices with changes: {stats['devices_with_changes']}")

        if stats["summary"]:
            self.logger.info("")
            self.logger.info("SUMMARY BY COMPONENT TYPE")
            self.logger.info("-" * 40)
            for comp_type, counts in stats["summary"].items():
                self.logger.info(f"{comp_type}:")
                self.logger.info(f"  - Added:     {counts['added']}")
                self.logger.info(f"  - Removed:   {counts['removed']}")
                self.logger.info(f"  - Protected: {counts['protected']}")

        if stats["errors"]:
            self.logger.info("")
            self.logger.info("ERRORS")
            self.logger.info("-" * 40)
            for error in stats["errors"]:
                self.logger.error(f"Device {error['device']}: {error['error']}")

        self.logger.info("=" * 60)

    def _export_json_report(self, stats: Dict):
        """
        Export the report as JSON.

        Args:
            stats: Statistics dictionary
        """
        try:
            # Convert defaultdict to regular dict for JSON serialization
            stats_copy = dict(stats)
            stats_copy["summary"] = dict(stats_copy["summary"])

            report_json = json.dumps(stats_copy, indent=2)

            self.logger.info("")
            self.logger.info("JSON REPORT")
            self.logger.info("-" * 40)
            self.logger.info(report_json)

        except Exception as e:
            self.logger.error(f"Failed to export JSON report: {e}")


# Register the job
register_jobs(SyncDeviceComponents)
