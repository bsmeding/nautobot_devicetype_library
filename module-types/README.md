# Module Types Import

This directory contains YAML files for importing module types into Nautobot.

## Directory Structure

```
module-types/
├── README.md
├── sample_modules.yaml
└── [manufacturer]/
    └── [module_types].yaml

module-images/
├── Cisco/
│   ├── cisco-c9300-nm-8x.front.png
│   ├── cisco-c9300-nm-8x.rear.png
│   ├── cisco-c9300-nm-4m.front.png
│   └── cisco-c9300-nm-4m.rear.png
├── Juniper/
│   ├── juniper-ex4300-48mp.front.png
│   └── juniper-ex4300-48mp.rear.png
└── Arista/
    ├── arista-dcs-7280sr3-48yc8.front.png
    └── arista-dcs-7280sr3-48yc8.rear.png
```

## YAML File Format

Each YAML file should contain a list of module types with the following structure:

```yaml
- manufacturer: "Cisco"
  model: "C9300-NM-8X"
  part_number: "C9300-NM-8X"
  description: "Cisco Catalyst 9300 8-Port 10G Network Module"
  weight: 0.5
  weight_unit: "kg"
  is_full_depth: false
```

### Required Fields

- `manufacturer`: The manufacturer name
- `model`: The module model name

### Optional Fields

- `part_number`: The part number (defaults to model if not specified)
- `description`: Description of the module
- `weight`: Weight of the module
- `weight_unit`: Weight unit (defaults to "kg")
- `is_full_depth`: Whether the module is full depth (defaults to true)

## Image Naming Convention

Module images should be named using the following pattern:

- Front images: `[manufacturer-slug]-[model-slug].front.[png|jpg|jpeg]`
- Rear images: `[manufacturer-slug]-[model-slug].rear.[png|jpg|jpeg]`

Examples:
- `cisco-c9300-nm-8x.front.png`
- `cisco-c9300-nm-8x.rear.png`
- `juniper-ex4300-48mp.front.jpg`

## Usage

1. Create YAML files in the `module-types/` directory
2. Add module images to the appropriate manufacturer directory in `module-images/`
3. Run the "Import Module Types" job in Nautobot
4. Use filters to import specific manufacturers or models

## Job Parameters

- `text_filter`: Regex filter for module model names
- `manufacturer`: Filter by specific manufacturer
- `dry_run`: Preview mode (no changes made)
- `debug_mode`: Enable detailed logging
- `include_images`: Include module images during import
