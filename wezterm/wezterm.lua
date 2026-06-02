local wezterm = require 'wezterm'

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
  config.leader = { key = 'a', mods = 'CMD', timeout_milliseconds = 1000 }

  keys = {
    { key = 'v', mods = 'LEADER',     action = wezterm.action.SplitHorizontal { domain = 'CurrentPaneDomain' } },
    { key = 'h', mods = 'LEADER',     action = wezterm.action.SplitVertical   { domain = 'CurrentPaneDomain' } },
    { key = 'x', mods = 'LEADER',     action = wezterm.action.CloseCurrentPane { confirm = true } },
    { key = 'LeftArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Left'  },
    { key = 'RightArrow', mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Right' },
    { key = 'UpArrow',    mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Up'    },
    { key = 'DownArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Down'  },
    { key = 'c', mods = 'CMD', action = wezterm.action.CopyTo 'Clipboard' },
    { key = 'v', mods = 'CMD', action = wezterm.action.PasteFrom 'Clipboard' },
  }

  config.mouse_bindings = {
    { event = { Up = { streak = 1, button = 'Left' } }, mods = 'CMD', action = wezterm.action.OpenLinkAtMouseCursor },
  }

else
  config.leader = { key = 'a', mods = 'ALT', timeout_milliseconds = 1000 }

  keys = {
    { key = 'v', mods = 'LEADER',     action = wezterm.action.SplitHorizontal { domain = 'CurrentPaneDomain' } },
    { key = 'h', mods = 'LEADER',     action = wezterm.action.SplitVertical   { domain = 'CurrentPaneDomain' } },
    { key = 'x', mods = 'LEADER',     action = wezterm.action.CloseCurrentPane { confirm = true } },
    { key = 'LeftArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Left'  },
    { key = 'RightArrow', mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Right' },
    { key = 'UpArrow',    mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Up'    },
    { key = 'DownArrow',  mods = 'LEADER', action = wezterm.action.ActivatePaneDirection 'Down'  },
    { key = 'c', mods = 'CTRL|SHIFT', action = wezterm.action.CopyTo 'Clipboard' },
    { key = 'v', mods = 'CTRL|SHIFT', action = wezterm.action.PasteFrom 'Clipboard' },
    { key = 'p', mods = 'CTRL|SHIFT', action = wezterm.action.SpawnCommandInNewTab {
        args = { 'powershell.exe', '-NoLogo' },
    }},
    { key = 'u', mods = 'CTRL|SHIFT', action = wezterm.action.SpawnCommandInNewTab {
        args = { 'wsl.exe', '~' },
    }},
  }

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
config.window_padding = { left = '16px', right = '16px', top = '14px', bottom = '14px' }

config.font = wezterm.font_with_fallback({
  { family = 'JetBrains Mono', weight = 'Regular' },
  'Symbols Nerd Font',
})
config.font_size = 11.5
config.line_height = 1.15

config.initial_cols = 150
config.initial_rows = 25
config.default_cursor_style = 'BlinkingBlock'
config.animation_fps = 1
config.scrollback_lines = 10000
config.enable_scroll_bar = false

return config