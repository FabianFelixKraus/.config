-- Pull in the wezterm API
local wezterm = require 'wezterm'

-- This will hold the configuration.
local config = wezterm.config_builder()

--------------------------------------------------------------------------------
-- 0. PLATFORM DETECTION
--------------------------------------------------------------------------------
local is_windows = wezterm.target_triple:find("windows") ~= nil
local is_macos = wezterm.target_triple:find("darwin") ~= nil

-- Default to PowerShell on Windows (WSL only opens when you explicitly ask)
if is_windows then
  config.default_prog = { 'powershell.exe', '-NoLogo' }
end

--------------------------------------------------------------------------------
-- 1. KEYBINDINGS & MULTIPLEXING CONTROLS (Platform-Specific)
--------------------------------------------------------------------------------
local keys = {}

if is_macos then
  config.send_composed_key_when_left_alt_is_pressed = true
  config.send_composed_key_when_right_alt_is_pressed = true
  -- macOS Leader: CMD + a
  config.leader = { key = 'a', mods = 'CMD', timeout_milliseconds = 1000 }

  keys = {
    { key = 'v', mods = 'LEADER',     action = wezterm.action.SplitHorizontal { domain = 'CurrentPaneDomain' } },
    { key = 'h', mods = 'LEADER',     action = wezterm.action.SplitVertical   { domain = 'CurrentPaneDomain' } },
    { key = 'x', mods = 'LEADER',     action = wezterm.action.CloseCurrentPane { confirm = true } },
    { key = 'LeftArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Left'  },
    { key = 'RightArrow', mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Right' },
    { key = 'UpArrow',    mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Up'    },
    { key = 'DownArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Down'  },
    -- macOS native copy/paste
    { key = 'c', mods = 'CMD', action = wezterm.action.CopyTo 'Clipboard' },
    { key = 'v', mods = 'CMD', action = wezterm.action.PasteFrom 'Clipboard' },
  }

  config.mouse_bindings = {
    { event = { Up = { streak = 1, button = 'Left' } }, mods = 'CMD', action = wezterm.action.OpenLinkAtMouseCursor },
  }

else
  -- Windows & Linux Leader: ALT + a
  config.leader = { key = 'a', mods = 'ALT', timeout_milliseconds = 1000 }

  keys = {
    { key = 'v', mods = 'LEADER',     action = wezterm.action.SplitHorizontal { domain = 'CurrentPaneDomain' } },
    { key = 'h', mods = 'LEADER',     action = wezterm.action.SplitVertical   { domain = 'CurrentPaneDomain' } },
    { key = 'x', mods = 'LEADER',     action = wezterm.action.CloseCurrentPane { confirm = true } },
    { key = 'LeftArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Left'  },
    { key = 'RightArrow', mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Right' },
    { key = 'UpArrow',    mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Up'    },
    { key = 'DownArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Down'  },
    
    -- Linux/Windows requires SHIFT to avoid overriding SIGINT (process kill)
    { key = 'c', mods = 'CTRL|SHIFT', action = wezterm.action.CopyTo 'Clipboard' },
    { key = 'v', mods = 'CTRL|SHIFT', action = wezterm.action.PasteFrom 'Clipboard' },
  }
  
  if is_windows then
    table.insert(keys, { key = 'p', mods = 'CTRL|SHIFT', action = wezterm.action.SpawnCommandInNewTab { args = { 'powershell.exe', '-NoLogo' } }})
    table.insert(keys, { key = 'u', mods = 'CTRL|SHIFT', action = wezterm.action.SpawnCommandInNewTab { args = { 'wsl.exe', '~' } }})
  end

  config.mouse_bindings = {
    { event = { Up = { streak = 1, button = 'Left' } }, mods = 'CTRL', action = wezterm.action.OpenLinkAtMouseCursor },
  }
end

config.keys = keys

--------------------------------------------------------------------------------
-- 2. THEME, TYPOGRAPHY & COGNITIVE FLOW
--------------------------------------------------------------------------------
config.color_scheme = 'Catppuccin Macchiato'
config.window_decorations = "RESIZE"

config.font = wezterm.font_with_fallback({
  { family = 'JetBrains Mono', weight = 'Regular' },
  'Symbols Nerd Font',
})
-- Your updated local preferences
config.font_size = 14.5
config.line_height = 1.15
config.initial_cols = 120
config.initial_rows = 30

config.default_cursor_style = 'BlinkingBlock'
config.animation_fps = 1
config.scrollback_lines = 10000
config.enable_scroll_bar = false

return config
