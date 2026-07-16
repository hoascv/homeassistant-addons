# Home Assistant Add-ons

Personal Home Assistant add-on repository.

## Add-ons

- **[Coop Tracker](coop-tracker/DOCS.md)** — log egg collection, coop
  cleaning, and feeding for your chickens from your phone.

## Installing this repository

1. In Home Assistant: **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top right) → **Repositories**.
3. Add the URL of this repository (once pushed to a Git host, e.g.
   `https://github.com/hoascv/homeassistant-addons`).
4. Find **Coop Tracker** in the store and install it.

### Testing locally without Git

If your Home Assistant host exposes a `/addons` share (e.g. via the Samba or
SSH & Web Terminal add-on), copy the `coop-tracker` folder there directly:

```
/addons/coop-tracker/
```

Then go to **Settings → Add-ons → Add-on Store**, click **⋮ → Check for
updates**, and **Coop Tracker** will appear under "Local add-ons".
