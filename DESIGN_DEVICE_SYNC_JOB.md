# Design : Job de synchronisation Device ↔ Device Type

## 🎯 Objectif

Créer un job Nautobot permettant de synchroniser les **Devices existants** avec leur **Device Type** en ajoutant/supprimant/comparant les composants (interfaces, ports, etc.).

---

## 📋 Fonctionnalités requises

### 1. Sélection des devices

Plusieurs critères combinables :
- **Par Device Type** : Tous les devices d'un type donné
- **Par Site/Location** : Tous les devices d'un site
- **Par Tag** : Devices avec un tag spécifique
- **Device unique** : Un seul device par nom ou ID

### 2. Modes de synchronisation

| Mode | Description | Action |
|------|-------------|--------|
| `diff` | Affiche les différences | Aucune modification (défaut) |
| `add` | Ajoute composants manquants | Création uniquement |
| `remove` | Supprime composants en trop | Suppression uniquement |
| `sync` | Synchronisation complète | add + remove |

### 3. Types de composants supportés

- ✅ **Interfaces** (InterfaceTemplate → Interface)
- ✅ **Console Ports** (ConsolePortTemplate → ConsolePort)
- ✅ **Console Server Ports** (ConsoleServerPortTemplate → ConsoleServerPort)
- ✅ **Power Ports** (PowerPortTemplate → PowerPort)
- ✅ **Power Outlets** (PowerOutletTemplate → PowerOutlet)
- ✅ **Front/Rear Ports** (FrontPortTemplate/RearPortTemplate → FrontPort/RearPort)
- ✅ **Device Bays** (DeviceBay template → DeviceBay)
- ⚠️ **Inventory Items** (optionnel, plus complexe)

### 4. Règles de protection

**Ne jamais toucher aux composants :**
- Avec des câbles connectés
- Avec des IP assignées (interfaces)
- Avec des VLANs configurés (interfaces)
- Avec des configurations custom importantes

**Option de force** :
- `--force` : Permet de bypasser les protections (avec confirmation)

### 5. Reporting détaillé

**Pendant l'exécution :**
- Progression par device (1/100, 2/100, ...)
- Composants ajoutés/supprimés par device

**Rapport final :**
- Résumé par type de composant
- Liste des erreurs rencontrées
- Statistiques globales
- Export JSON/CSV optionnel

---

## 🏗️ Architecture technique

### Classes principales

```python
# jobs/device_sync.py

class SyncDeviceComponents(Job):
    """Synchronise les composants d'un Device avec son Device Type"""

    # Paramètres de sélection
    device_type = MultiObjectVar(...)
    site = MultiObjectVar(...)
    location = MultiObjectVar(...)
    tags = MultiObjectVar(...)
    device = ObjectVar(...)

    # Paramètres d'action
    sync_mode = ChoiceVar(...)  # diff, add, remove, sync
    component_types = MultipleChoiceVar(...)  # interfaces, console-ports, etc.

    # Options
    protect_connected = BooleanVar(default=True)
    protect_configured = BooleanVar(default=True)
    force = BooleanVar(default=False)
    export_report = BooleanVar(default=False)

    def run(self, **kwargs):
        # 1. Collecter les devices selon les critères
        devices = self._collect_devices(**kwargs)

        # 2. Pour chaque device
        for device in devices:
            # 3. Comparer device vs device_type
            diff = self._compute_diff(device)

            # 4. Appliquer les changements selon le mode
            if sync_mode != "diff":
                self._apply_changes(device, diff, sync_mode)

        # 5. Générer le rapport
        self._generate_report()

class ComponentDiff:
    """Représente les différences entre Device et DeviceType"""
    def __init__(self, device, device_type):
        self.device = device
        self.device_type = device_type
        self.to_add = {}      # {component_type: [templates]}
        self.to_remove = {}   # {component_type: [instances]}
        self.unchanged = {}   # {component_type: [instances]}

class ComponentSyncer:
    """Gère la synchronisation d'un type de composant"""
    def __init__(self, component_type, template_model, instance_model):
        self.component_type = component_type
        self.template_model = template_model
        self.instance_model = instance_model

    def compute_diff(self, device):
        """Calcule les différences pour ce type de composant"""

    def add_components(self, device, templates):
        """Ajoute les composants manquants"""

    def remove_components(self, device, instances, force=False):
        """Supprime les composants en trop"""

    def is_protected(self, component):
        """Vérifie si le composant est protégé"""
```

---

## 🔍 Algorithme de détection des différences

### Comparaison par nom (clé primaire)

```python
def compute_diff(self, device):
    # 1. Récupérer les templates du device type
    templates = self.template_model.objects.filter(device_type=device.device_type)
    template_names = {t.name: t for t in templates}

    # 2. Récupérer les composants existants du device
    instances = self.instance_model.objects.filter(device=device)
    instance_names = {i.name: i for i in instances}

    # 3. Calculer les différences
    to_add = set(template_names.keys()) - set(instance_names.keys())
    to_remove = set(instance_names.keys()) - set(template_names.keys())
    unchanged = set(template_names.keys()) & set(instance_names.keys())

    # 4. Construire le diff
    diff = {
        "to_add": [template_names[name] for name in to_add],
        "to_remove": [instance_names[name] for name in to_remove],
        "unchanged": [instance_names[name] for name in unchanged]
    }

    return diff
```

### Vérification des propriétés (optionnel)

Pour chaque composant dans `unchanged`, vérifier si les propriétés correspondent :

```python
for name in unchanged:
    template = template_names[name]
    instance = instance_names[name]

    if instance.type != template.type:
        diff["to_update"].append({
            "instance": instance,
            "template": template,
            "changes": {"type": (instance.type, template.type)}
        })
```

---

## 🛡️ Règles de protection

### 1. Protection des composants connectés

```python
def is_connected(component):
    """Vérifie si le composant a des câbles connectés"""
    if isinstance(component, Interface):
        return Cable.objects.filter(
            Q(_termination_a_device=component.device,
              _termination_a_name=component.name) |
            Q(_termination_b_device=component.device,
              _termination_b_name=component.name)
        ).exists()

    # Similaire pour PowerPort, ConsolePort, etc.
    return component.cable is not None
```

### 2. Protection des composants configurés

```python
def is_configured(interface):
    """Vérifie si l'interface a une configuration"""
    if interface.ip_addresses.exists():
        return True
    if interface.untagged_vlan or interface.tagged_vlans.exists():
        return True
    if interface.description and interface.description.strip():
        return True
    return False
```

### 3. Mode force

```python
def remove_component(self, component, force=False):
    if self.is_protected(component) and not force:
        raise ProtectedComponentError(
            f"{component} is protected (connected or configured)"
        )

    component.delete()
```

---

## 📊 Structure du rapport

### Format JSON

```json
{
  "job_id": "12345",
  "timestamp": "2025-12-22T10:30:00Z",
  "mode": "sync",
  "devices_processed": 100,
  "devices_succeeded": 95,
  "devices_failed": 5,
  "summary": {
    "interfaces": {
      "added": 250,
      "removed": 30,
      "protected": 15
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
      "status": "success",
      "changes": {
        "interfaces": {
          "added": ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2"],
          "removed": [],
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

### Format texte (log)

```
========================================
Device Component Synchronization Report
========================================

Mode: sync
Devices processed: 100/100
Success rate: 95%

SUMMARY BY COMPONENT TYPE
--------------------------
Interfaces:
  - Added:     250
  - Removed:    30
  - Protected:  15

Console Ports:
  - Added:     100
  - Removed:     0
  - Protected:   0

DEVICES WITH CHANGES (10 shown, 95 total)
------------------------------------------
✓ switch01.example.com
  + 2 interfaces added
  - 0 interfaces removed

✓ switch02.example.com
  + 48 interfaces added
  - 4 interfaces removed

✗ switch05.example.com
  ERROR: DeviceType not found

...
```

---

## 🔧 Paramètres du job

### Formulaire Nautobot

```python
class SyncDeviceComponents(Job):
    # --- SELECTION DES DEVICES ---
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

    # --- MODE DE SYNCHRONISATION ---
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

    # --- OPTIONS DE PROTECTION ---
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
        approval_required = False  # Mettre True pour les syncs massifs
        soft_time_limit = 1800  # 30 minutes
        time_limit = 2000
```

---

## 🧪 Cas d'usage et exemples

### Exemple 1 : Voir les différences pour un device type

**Paramètres :**
- Device Type: `Cisco Catalyst 9200L-48P-4G`
- Mode: `diff`

**Résultat :**
```
Device: switch01.example.com
  Missing interfaces (to add):
    - GigabitEthernet1/0/1
    - GigabitEthernet1/0/2
    ...
  Extra interfaces (to remove):
    - OldInterface1 (not in device type)
```

### Exemple 2 : Ajouter les interfaces manquantes

**Paramètres :**
- Site: `DC-Paris`
- Device Type: `Cisco Catalyst 9200L-48P-4G`
- Mode: `add`
- Component Types: `interfaces`

**Résultat :**
```
Processed 50 devices
Added 1200 interfaces
Protected 25 interfaces (connected)
```

### Exemple 3 : Synchronisation complète d'un device

**Paramètres :**
- Device: `switch01.example.com`
- Mode: `sync`
- Component Types: `interfaces`, `console_ports`, `power_ports`

**Résultat :**
```
Device: switch01.example.com
  Interfaces:
    + Added: 48
    - Removed: 2
    ⚠ Protected: 1 (mgmt0 has IP addresses)
  Console Ports:
    + Added: 2
  Power Ports:
    (no changes)
```

---

## ⚠️ Considérations de sécurité

### 1. Validation des entrées
- Au moins un critère de sélection requis
- Confirmation pour mode `remove` ou `sync`
- Warning si `force=True`

### 2. Gestion des permissions
- Vérifier que l'utilisateur a les permissions DCIM appropriées
- Audit log de toutes les modifications

### 3. Rate limiting
- Batch processing (100 devices max par run)
- Option de dry-run toujours disponible (mode diff)

---

## 🚀 Plan d'implémentation

### Phase 2A : Core (CETTE PHASE)
1. ✅ Design complet (ce document)
2. 🔄 Créer `jobs/utils.py` (utilitaires communs)
3. 🔄 Créer `jobs/device_sync.py` (job principal)
4. 🔄 Implémenter `ComponentSyncer` pour interfaces
5. 🔄 Implémenter protection des composants
6. 🔄 Tests basiques

### Phase 2B : Extension (OPTIONNEL)
7. Étendre à tous les types de composants
8. Implémenter export JSON/CSV
9. Ajouter support des inventory items
10. Tests complets

---

**Next steps** : Implémentation de `jobs/utils.py` puis `jobs/device_sync.py`
