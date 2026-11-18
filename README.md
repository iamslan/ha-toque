# Toque for Home Assistant

A Home Assistant custom integration for a Lidl **Monsieur Cuisine Connect** cooking robot that
runs [Toque](https://github.com/iamslan/MrCuisineVibeConnect), the open-source replacement app
for the robot's tablet.

It shows in Home Assistant what the robot is doing (program, time left, temperature, weight,
the current step of a guided recipe), tells you when the robot is waiting for you, and lets
your automations search the robot's recipe library and send a recipe's ingredients to a
shopping list.

- **Read-only.** The integration only reads. Nothing in it, and nothing in Toque's home link
  API, can start, change, pause or stop the machine.
- **Local.** Home Assistant talks to the robot directly over your home network (plain HTTP,
  port 8730). No cloud, no account.
- **Push.** The robot sends its changes as they happen; nothing is polled while it is on.

A robot that is switched off is a normal situation: its entities simply become *unavailable*
and come back by themselves when it is switched on again.

## Requirements

- Home Assistant 2025.2 or later.
- A Monsieur Cuisine Connect running a version of Toque that has the home link
  (Settings › Home link).
- The robot and Home Assistant on the same network.

## Installation

### With HACS (custom repository)

1. In HACS, open the menu (⋮) › **Custom repositories**.
2. Add the repository `iamslan/ha-toque` with the type **Integration**.
3. Find **Toque** in HACS, download it, then restart Home Assistant.

### By hand

1. Copy the folder `custom_components/toque` of this repository into the `custom_components`
   folder of your Home Assistant configuration (next to `configuration.yaml`).
2. Restart Home Assistant.

## Pairing

1. On the robot, open **Toque › Settings › Home link** and switch the home link on.
2. In Home Assistant, the robot is normally discovered by itself: open
   **Settings › Devices & services** and choose **Add** on the discovered *Toque*.
   If it is not discovered, choose **Add integration › Toque** and enter the address of the
   robot (shown on the robot's Home link screen) and the port (8730).
3. On the robot, choose **Toque › Settings › Home link › Pair a device**. A 6-digit code
   appears; it is valid for 5 minutes.
4. Enter the code in Home Assistant.

After 5 wrong codes the robot cancels the code: show a new one with *Pair a device*.

If you choose *Unpair all devices* on the robot, Home Assistant loses its access and asks you
to pair again (a "reconfigure" notification appears on the integration).

## Entities

One device is created per robot. Texts coming from the robot (program label, recipe title,
step text) are in the language of the robot's interface.

### Sensors

| Entity | What it shows |
|---|---|
| State | `offline`, `idle`, `running`, `paused`, `waiting_for_user` or `fault`. Attribute `mode` (`hardware`, `demo`, `twin`). |
| Program | Kind of the loaded program: `manual`, `knead`, `steam`, `toast`, `turbo`. Attributes: `job_state`, `pause_reason`, `total`, `phase`, `phases`, `reverse`, `accessory`. |
| Program label | The program's name as shown on the robot. |
| Time remaining | Seconds left in the program. The robot does not send a status every second, so this is not a smooth countdown: use *End time* for that. |
| End time | When the running program will end (timestamp). Only set while a program is running. |
| Bowl temperature | °C. The motor base measures nothing under 37 °C: the state is then *unknown* and the attribute `below_range` is `true`. |
| Target temperature | °C, unknown without heating. |
| Speed | The speed setting. |
| Weight | Grams on the scale (may be negative after a tare). Attribute `stable`. |
| Recipe | Title of the guided recipe in progress. Attribute `recipe_id`. |
| Recipe step | Number of the current step, from 1. |
| Recipe step count | Number of steps of the recipe. |
| Recipe step text | Text of the current step, cut at 255 characters. Attributes `full_text` (whole text), `step_kind` (`machine`, `weigh`, `text`), `weigh_target`. |
| Recipe step setting | Short summary of a machine step, for example `00:30 · vitesse 8`. |
| Rotation speed | Blade speed in rpm. *Disabled by default.* |
| Motor base link | Link between the tablet and the motor base: `offline`, `connecting`, `ready`, `fault`. *Diagnostic, disabled by default.* |
| Motor base firmware | Firmware number of the motor base. *Diagnostic, disabled by default.* |

### Binary sensors

| Entity | On when |
|---|---|
| Lid | the lid is open. |
| Bowl present | the bowl is in place. *Unknown* when the robot's bowl sensor is not reliable (attribute `sensor_reliable`). |
| Motor | the motor is running. |
| Heating | the heater is on. |
| Waiting for the cook | a guided recipe is open and the robot waits for you to do the next step. |
| Fault | the machine reports a fault. Attributes `code` and `message`. |

### Event entity

**Cooking event** (`event.<robot>_cooking_event`) fires for each of these event types; the
data sent by the robot become the entity's attributes.

| Event type | When | Data |
|---|---|---|
| `job_started` | a program starts | `time`, `kind`, `label`, `total`, `guided` |
| `job_paused` | a program pauses | `time`, `kind`, `label`, `reason` |
| `job_resumed` | a program resumes | `time`, `kind`, `label` |
| `job_finished` | a program runs to its end | `time`, `kind`, `label`, `guided` |
| `job_aborted` | a program is stopped before its end | `time`, `kind`, `label`, `reason` |
| `waiting_for_user` | a guided step has finished and the robot waits for the cook | `time`, `guided` (the step now waiting) |
| `recipe_finished` | the last machine step of a guided recipe has finished | `time`, `recipe_id`, `recipe_title` |
| `lid_opened`, `lid_closed` | the lid changes | `time` |
| `fault` | the machine reports a fault | `time`, `code`, `message` |

`time` is Unix time in seconds. `guided` is `null` outside a guided recipe, otherwise an object
with `recipe_id`, `recipe_title`, `step`, `steps`, `step_text`, `step_kind`, `step_setting`,
`weigh_target` and `waiting_for_user`.

Events are not replayed: what happened while Home Assistant or the robot was disconnected is
not fired afterwards.

### Events on the event bus

The same events are also fired on the Home Assistant event bus as **`toque_event`**, with the
robot's data plus:

- `type`: the event type (`job_started`, `waiting_for_user`, …);
- `device_id`: the Home Assistant device of the robot;
- `entry_id`: the config entry of the robot.

Use whichever you prefer in automations: the event entity or the bus event.

## Actions (services)

When a single robot is configured, it is implicit. With several robots, add
`config_entry_id` or `device_id` to say which one.

### `toque.search_recipes`

Searches the robot's library and returns `{"total": …, "items": […]}`.

| Field | Meaning |
|---|---|
| `query` | Words to look for. Empty: all recipes, favourites first. |
| `category` | Keep one category, by name. |
| `favorites` | `true` to keep favourites only. |
| `limit` | 1 to 200, default 50. |
| `offset` | Recipes to skip, default 0. |

```yaml
action: toque.search_recipes
data:
  query: risotto
  limit: 5
response_variable: found
# found['items'][0].id, found['items'][0].title, found['items'][0].minutes, ...
# (write found['items'], not found.items: in a template .items is a dictionary method)
```

### `toque.get_recipe`

Returns `{"recipe": {…}}`: the whole recipe with its ingredients and steps.

```yaml
action: toque.get_recipe
data:
  recipe_id: web-250008
response_variable: result
# result.recipe.title, result.recipe.ingredients, result.recipe.steps
```

### `toque.add_to_shopping_list`

Adds one to-do item per ingredient (the ready-to-read line, for example
`300 g cacahuètes grillées, non salées`) to a to-do list, and returns
`{"added": <number of items>, "recipes": [{"id", "title", "added"}]}`.

| Field | Meaning |
|---|---|
| `recipe_id` | One recipe id, or a list of ids. |
| `recipe_ids` | A list of recipe ids (alternative to `recipe_id`). |
| `todo_entity` | The to-do list entity, for example `todo.shopping_list`. |

All recipes are read first: if one id is unknown, nothing is added.

## Example automations

### Notify a phone when the robot waits for you

```yaml
automation:
  - alias: "Toque: the robot is waiting for me"
    triggers:
      - trigger: event
        event_type: toque_event
        event_data:
          type: waiting_for_user
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: >-
            {{ trigger.event.data.guided.recipe_title if trigger.event.data.guided else 'Toque' }}
          message: >-
            {% set guided = trigger.event.data.guided %}
            {% if guided %}
            Step {{ guided.step }}/{{ guided.steps }}: {{ guided.step_text }}
            {% else %}
            The robot is waiting for you.
            {% endif %}
```

The same with the event entity instead of the bus event (replace the entity id with yours):

```yaml
    triggers:
      - trigger: state
        entity_id: event.toque_cooking_event
        not_from: unavailable
    conditions:
      - condition: state
        entity_id: event.toque_cooking_event
        attribute: event_type
        state: waiting_for_user
    # the step is then in state_attr('event.toque_cooking_event', 'guided').step_text
```

### Add a recipe's ingredients to the shopping list

A script that looks for a recipe by name and adds the ingredients of the first result:

```yaml
script:
  toque_shop_for_recipe:
    alias: "Toque: add a recipe to the shopping list"
    fields:
      recipe_name:
        description: Name of the recipe to look for
        example: Beurre de cacahuètes
        selector:
          text:
    sequence:
      - action: toque.search_recipes
        data:
          query: "{{ recipe_name }}"
          limit: 1
        response_variable: found
      - condition: template
        value_template: "{{ found['items'] | count > 0 }}"
      - action: toque.add_to_shopping_list
        data:
          recipe_id: "{{ found['items'][0].id }}"
          todo_entity: todo.shopping_list
        response_variable: result
      - action: persistent_notification.create
        data:
          title: Shopping list
          message: >-
            {{ result.added }} ingredients of "{{ found['items'][0].title }}" were added.
```

If you already know the recipe's id, one action is enough:

```yaml
action: toque.add_to_shopping_list
data:
  recipe_id: web-250008
  todo_entity: todo.shopping_list
```

## Dashboard and recipe browser

Two ready-made files in [`examples/`](examples/) give you a dashboard view and a recipe browser
with a shopping list. They use built-in Home Assistant cards and helpers only, with French
labels.

### 1. The recipe browser: `examples/toque_package.yaml`

Home Assistant has no card that can show the answer of an action, so this package adds the
missing pieces: a search field, a list of the recipes found, and two scripts.

1. In `configuration.yaml`, enable packages if you have not already:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy `examples/toque_package.yaml` to `<config>/packages/toque.yaml`.
3. In that file, set your to-do list (search for `CHANGE ME`). Home Assistant's own shopping
   list is `todo.liste_de_courses` in French and `todo.shopping_list` in English.
4. Restart Home Assistant.

It creates `input_text.toque_recherche`, `input_select.toque_recette`,
`sensor.toque_recettes_trouvees`, `sensor.toque_recette_choisie`, `script.toque_chercher` and
`script.toque_ajouter_au_panier`.

### 2. The dashboard view: `examples/toque_dashboard.yaml`

Open a dashboard, switch to edit mode, add a view, choose **Edit in YAML** and paste the file.

The file assumes the robot's device is named `MrCuisine` in a French Home Assistant, which
gives entity ids such as `sensor.mrcuisine_etat`. If yours differ (see the device page),
replace `mrcuisine_` everywhere with your own prefix. Change `todo.liste_de_courses` as well if
your list has another id.

The view has three columns:

- **Robot:** state, bowl temperature, scale, lid, bowl, fault.
- **Cuisson:** the running program with its end time, a "your turn" banner when the robot
  waits for you, and the current step of a guided recipe with its whole text.
- **Recettes:** search, pick, read the ingredients and steps, add to the shopping list.

### Browsing recipes and filling the shopping list

1. Type a few words in **Chercher** (for example `soupe potiron`) and press **Chercher**.
   Leave the field empty to list everything, favourites first.
2. Open **Recette** and pick one of the recipes found. Its ingredients and steps appear below.
3. Press **Ajouter au panier**: one to-do item is added per ingredient, and a notification says
   how many.

The robot must be switched on: the recipes live on the robot, not in Home Assistant. The list
shows the first 20 recipes found; refine the words to narrow it down.

The same thing without any dashboard, from **Developer tools › Actions**:

```yaml
action: toque.search_recipes
data:
  query: soupe potiron
  limit: 10
```

then, with an `id` from the answer:

```yaml
action: toque.add_to_shopping_list
data:
  recipe_id: web-10198496
  todo_entity: todo.liste_de_courses
```

## Troubleshooting

- **Entities are unavailable.** The robot is switched off, Toque is not running, or the home
  link is switched off in Toque › Settings › Home link. The integration keeps trying (every
  30 seconds at most) and recovers by itself.
- **"The robot does not answer" when adding.** Check the address shown on the robot's Home
  link screen and that both devices are on the same network.
- **The robot changed address.** It is found again by discovery and the address is updated.
  Giving the robot a fixed address in your router avoids the question.
- Debug logs:

  ```yaml
  logger:
    logs:
      custom_components.toque: debug
  ```

## Development

`custom_components/toque/api.py` is a standalone client (it imports nothing from Home
Assistant). Its tests run against a fake robot, without Home Assistant:

```
pip install -r requirements_test.txt
python -m pytest
```

`tests/fake_robot.py` is also a small program that plays a cooking scenario in a loop, to try
the integration in Home Assistant without a robot (manual entry only, it does not announce
itself on the network):

```
python tests/fake_robot.py --port 8730 --code 123456
```

## License

MIT, see [LICENSE](LICENSE).
