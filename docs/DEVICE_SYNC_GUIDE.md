# Device Component Synchronization Guide

## 📋 Overview

The **Sync Device Components** job allows you to synchronize network devices with their device type definitions in Nautobot. This ensures that devices have all the components (interfaces, ports, etc.) defined in their device type template.

### Common Use Cases

- 🔄 **Initial device provisioning**: Add all interfaces and ports when a new device is added
- 🔧 **Device type updates**: Synchronize existing devices after updating a device type
- 🧹 **Cleanup**: Remove components that don't match the device type
- 📊 **Audit**: Compare devices against templates without making changes

---

## 🚀 Quick Start

### Example 1: Preview changes for a single device

**Goal**: See what would change without modifying anything

**Steps**:
1. Navigate to **Jobs** → **Sync Device Components**
2. Select your device in the **Device** field
3. Set **Sync Mode** to **Diff only**
4. Click **Run Job**

**Result**: You'll see a report showing:
- Components that would be added
- Components that would be removed
- Protected components that won't be touched

### Example 2: Add missing interfaces to all devices of a type

**Goal**: Add missing interfaces to all Cisco Catalyst 9200 switches

**Steps**:
1. Navigate to **Jobs** → **Sync Device Components**
2. Select **Device Type**: `Cisco Catalyst 9200L-48P-4G`
3. Set **Sync Mode** to **Add missing components**
4. Select **Component Types**: `Interfaces`
5. Ensure **Protect connected** and **Protect configured** are checked
6. Click **Run Job**

**Result**: All missing interfaces will be created on matching devices

### Example 3: Full synchronization of a site

**Goal**: Synchronize all devices at a specific site

**Steps**:
1. Navigate to **Jobs** → **Sync Device Components**
2. Select **Site**: Your site
3. Set **Sync Mode** to **Full sync (add + remove)**
4. Select **Component Types**: All types you want to sync
5. Review protection options
6. Click **Run Job**

**Result**: All devices at the site will be synchronized with their device types

---

## 🎛️ Parameters Explained

### Device Selection

Select devices to synchronize using one or more criteria:

| Parameter | Description | Example |
|-----------|-------------|---------|
| **Device Type** | All devices of specific types | Cisco Catalyst 9200L-48P-4G |
| **Site** | All devices at specific sites | DC-Paris, DC-London |
| **Location** | All devices at specific locations | Building A / Floor 3 |
| **Tags** | Devices with specific tags | production, network-core |
| **Device** | Single device by name | switch01.example.com |

**Note**: If you select **Device**, all other selection criteria are ignored.

### Sync Modes

| Mode | Description | When to Use |
|------|-------------|-------------|
| **diff** | Show differences only | Audit, preview before changes |
| **add** | Add missing components | Initial provisioning, new templates |
| **remove** | Remove extra components | Cleanup after template changes |
| **sync** | Add + Remove (full sync) | Complete synchronization |

⚠️ **Best Practice**: Always run in **diff** mode first to preview changes!

### Component Types

Select which component types to synchronize:

- ✅ **Interfaces** - Network interfaces (Ethernet, Fiber, etc.)
- ✅ **Console Ports** - Physical console connections
- ✅ **Console Server Ports** - Remote console access ports
- ✅ **Power Ports** - Power input connections
- ✅ **Power Outlets** - Power output connections (PDUs)
- ✅ **Front/Rear Ports** - Pass-through ports
- ✅ **Device Bays** - Slots for child devices

**Tip**: For most cases, start with just **Interfaces**

### Protection Options

| Option | Default | Description |
|--------|---------|-------------|
| **Protect connected** | ✅ True | Don't remove components with cables |
| **Protect configured** | ✅ True | Don't remove configured components (IPs, VLANs) |
| **Force** | ❌ False | Override protection (⚠️ USE WITH CAUTION) |

#### What is "Configured"?

A component is considered configured if it has:

**For Interfaces:**
- IP addresses assigned
- VLAN configuration (tagged or untagged)
- LAG/bonding configuration
- Non-empty description

**For All Components:**
- Connected cables
- Non-empty description field

#### Force Mode ⚠️

When **Force** is enabled, the job will:
- Remove components even if they have cables
- Remove components even if they have IP addresses or VLANs
- Delete all components not in the device type

**⚠️ WARNING**: This can cause service disruptions! Only use when you're absolutely sure.

### Reporting

| Option | Description |
|--------|-------------|
| **Export report** | Generate detailed JSON report at the end |

---

## 📊 Understanding the Output

### Diff Mode Output

```
Device: switch01.example.com
Differences for switch01.example.com:
  + interfaces: 48 to add
    - GigabitEthernet1/0/1
    - GigabitEthernet1/0/2
    ...
  - interfaces: 2 to remove
    - OldInterface1
    - OldInterface2
  ⚠ interfaces: 1 protected
    - mgmt0
```

**Legend**:
- `+` Components that will be **added**
- `-` Components that will be **removed**
- `⚠` Components that are **protected** from removal

### Apply Changes Output

```
Processing device 1/50: switch01.example.com
Created 48 interfaces for switch01.example.com
Removed 2 interfaces from switch01.example.com
Skipping protected interfaces: mgmt0
```

### Final Report

```
========================================
DEVICE COMPONENT SYNCHRONIZATION REPORT
========================================
Mode: sync
Devices processed: 50
Devices succeeded: 48
Devices failed: 2
Devices with changes: 45

SUMMARY BY COMPONENT TYPE
--------------------------
interfaces:
  - Added:     1200
  - Removed:   45
  - Protected: 12

console_ports:
  - Added:     100
  - Removed:   0
  - Protected: 0

ERRORS
------
Device switch05.example.com: DeviceType not found
Device switch12.example.com: Permission denied
```

---

## 🎯 Common Scenarios

### Scenario 1: New Device Added to Nautobot

**Situation**: You just added a new Cisco Catalyst 9200 switch, but it has no interfaces.

**Solution**:
```
Device: switch-new-01
Sync Mode: add
Component Types: interfaces, console_ports, power_ports
```

**Result**: All interfaces, console ports, and power ports are created automatically.

---

### Scenario 2: Device Type Template Updated

**Situation**: You updated the Catalyst 9200 device type to add 4 new SFP+ uplink ports.

**Solution**:
```
Device Type: Cisco Catalyst 9200L-48P-4G
Sync Mode: add
Component Types: interfaces
```

**Result**: All existing Catalyst 9200 devices will get the 4 new uplink interfaces.

---

### Scenario 3: Device Migrated to New Type

**Situation**: A device was re-provisioned with different hardware (e.g., 48-port → 24-port).

**Solution**:
1. First, run in **diff** mode to see what will change:
   ```
   Device: switch01
   Sync Mode: diff
   Component Types: all
   ```

2. Review the output carefully

3. If acceptable, run in **sync** mode:
   ```
   Device: switch01
   Sync Mode: sync
   Component Types: all
   Protect connected: true
   Protect configured: true
   ```

**Result**: Extra ports are removed (unless connected), missing ports are added.

---

### Scenario 4: Cleanup After Decommissioning

**Situation**: Several devices were decommissioned but still have old interfaces/ports.

**Solution**:
```
Tags: decommissioned
Sync Mode: remove
Component Types: all
Force: true  # Only if you're sure!
```

**Result**: All components are removed from decommissioned devices.

---

### Scenario 5: Audit Compliance

**Situation**: You want to verify all devices match their templates.

**Solution**:
```
Site: DC-Production
Sync Mode: diff
Component Types: all
Export report: true
```

**Result**: Detailed JSON report showing all differences across the site.

---

## ⚠️ Safety and Best Practices

### Before Running the Job

✅ **DO**:
- Always run in **diff** mode first
- Review the output carefully
- Test on a single device before bulk operations
- Ensure device types are up-to-date
- Have a backup or change window

❌ **DON'T**:
- Use **remove** or **sync** mode without reviewing diff first
- Enable **force** mode unless absolutely necessary
- Run bulk operations during business hours (unless safe)
- Ignore protected component warnings

### Protection is Your Friend

The job protects critical components by default:

**Protected by default**:
- Interfaces with IP addresses
- Interfaces with VLAN tags
- Interfaces in LAG/bonding
- Any component with a cable connected

**Override protection only when**:
- You're absolutely sure it's safe
- You've verified no services depend on it
- You have a rollback plan

### Transaction Safety

The job uses database transactions:
- Each device is processed in its own transaction
- If an error occurs, changes to that device are rolled back
- Other devices continue processing
- No partial device states

---

## 🔧 Troubleshooting

### "No devices found matching the selection criteria"

**Cause**: Your filter criteria don't match any devices

**Solution**:
- Check if the device type / site / location exists
- Verify devices are assigned to the selected criteria
- Try selecting a single device to test

---

### "Device has no device type assigned"

**Cause**: A device in your selection has no device type

**Solution**:
- Assign a device type to the device first
- Or exclude that device from the selection

---

### "Protected component warnings"

**Cause**: Components you're trying to remove are protected

**Solution**:
- This is usually correct behavior - leave protected components alone
- If you really need to remove them:
  1. Disconnect cables first
  2. Remove IP addresses / VLANs
  3. Run the job again
  4. Or use **force** mode (⚠️ dangerous)

---

### "Permission denied"

**Cause**: Insufficient permissions to modify devices

**Solution**:
- Verify you have DCIM change permissions in Nautobot
- Contact your Nautobot administrator

---

## 📈 Performance Considerations

### Bulk Operations

The job uses bulk creation for performance:
- Creates components in batches of 100
- Much faster than one-by-one creation
- Suitable for large deployments

### Recommended Limits

| Devices | Recommended Approach |
|---------|---------------------|
| 1-10 | Run directly |
| 10-100 | Run during maintenance window |
| 100-1000 | Split into multiple jobs by site/type |
| 1000+ | Contact support for optimization advice |

### Job Timeout

- Soft limit: 30 minutes
- Hard limit: 33 minutes
- If you hit the limit, split your job into smaller batches

---

## 🔍 JSON Report Format

When **Export report** is enabled, you get a detailed JSON report:

```json
{
  "job_id": "12345",
  "timestamp": "2025-12-22T10:30:00Z",
  "mode": "sync",
  "component_types": ["interfaces", "console_ports"],
  "devices_processed": 100,
  "devices_succeeded": 95,
  "devices_failed": 5,
  "devices_with_changes": 80,
  "summary": {
    "interfaces": {
      "added": 1200,
      "removed": 45,
      "protected": 12
    },
    "console_ports": {
      "added": 100,
      "removed": 0,
      "protected": 0
    }
  },
  "devices": [
    {
      "device": "switch01.example.com",
      "device_type": "Cisco Catalyst 9200L-48P-4G",
      "status": "success",
      "changes": {
        "interfaces": {
          "to_add": ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2"],
          "to_remove": [],
          "protected": ["mgmt0"]
        }
      }
    }
  ],
  "errors": [
    {
      "device": "switch05.example.com",
      "error": "DeviceType not found"
    }
  ]
}
```

This report can be:
- Saved for audit purposes
- Parsed by automation tools
- Used for compliance reporting
- Analyzed for trends

---

## 📚 Related Documentation

- [Nautobot Device Types Documentation](https://docs.nautobot.com/projects/core/en/stable/user-guide/core-data-model/dcim/devicetype/)
- [Phase 1 Analysis Report](../ANALYSE_PHASE1.md)
- [Device Sync Job Design](../DESIGN_DEVICE_SYNC_JOB.md)

---

## 🤝 Support

If you encounter issues or have questions:

1. Check the troubleshooting section above
2. Review the job logs in Nautobot
3. Open an issue on GitHub with:
   - Job parameters used
   - Error messages
   - Expected vs actual behavior

---

**Last Updated**: 2025-12-22
**Job Version**: 1.0.0
**Compatible with**: Nautobot 2.x
