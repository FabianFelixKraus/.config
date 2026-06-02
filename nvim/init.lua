--------------------------------------------------------------------------------
-- 1. AUTOMATIC PLUGIN MANAGER BOOTSTRAP (The Magic Portability Trick)
--------------------------------------------------------------------------------
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.loop.fs_stat(lazypath) then
  vim.fn.system({
    "git", "clone", "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git", "--branch=stable", lazypath,
  })
end
vim.opt.rtp:prepend(lazypath)

--------------------------------------------------------------------------------
-- 2. PLUGIN LIST (Modern, Healthy & Smart Extensions)
--------------------------------------------------------------------------------
require("lazy").setup({
  -- 1. Theme loads FIRST
  {
    "catppuccin/nvim",
    name = "catppuccin",
    priority = 1000,
    config = function()
      require("catppuccin").setup({
        flavour = "macchiato",
        transparent_background = true,
      })
      vim.cmd.colorscheme("catppuccin")
    end
  },

  -- 2. File Explorer Sidebar
  {
    "nvim-tree/nvim-tree.lua",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    config = function() require("nvim-tree").setup() end
  },

  -- 3. Statusline (Forced to wait for Catppuccin)
  {
    "nvim-lualine/lualine.nvim",
    dependencies = { "nvim-tree/nvim-web-devicons", "catppuccin" },
    config = function()
      require("lualine").setup({
        options = { theme = "auto" }
    })
    end
  },

  -- 4. Syntax Highlighting
  {
    "nvim-treesitter/nvim-treesitter",
    build = ":TSUpdate",
    config = function()
      require("nvim-treesitter.config").setup({
        ensure_installed = { "lua", "python", "sql", "bash", "markdown", "json" },
        highlight = { enable = true },
      })
    end
  },

  -- 5. Autopairs
  {
    "windwp/nvim-autopairs",
    event = "InsertEnter",
    config = function() require("nvim-autopairs").setup({}) end
  }
})

--------------------------------------------------------------------------------
-- 3. NATIVE NEOVIM OPTIONS (Comfort, Ergonomics, & Health)
--------------------------------------------------------------------------------
vim.opt.number = true          -- Show line numbers
vim.opt.relativenumber = true  -- Relative numbers make jumping lines a breeze
vim.opt.mouse = 'a'            -- FULL trackpad/mouse integration
vim.opt.clipboard = 'unnamedplus' -- Syncs Neovim buffer directly with macOS clipboard

-- Indentation rules
vim.opt.expandtab = true
vim.opt.tabstop = 4
vim.opt.shiftwidth = 4
vim.opt.softtabstop = 4

-- Search settings
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.hlsearch = false

--------------------------------------------------------------------------------
-- 4. PRACTICAL KEYMAPS FOR QUICK NAVIGATION
--------------------------------------------------------------------------------
-- Set spacebar as your "leader" key
vim.g.mapleader = " "

-- Toggle File Explorer sidebar with Ctrl + n
vim.keymap.set("n", "<C-n>", ":NvimTreeToggle<CR>", { silent = true })

-- Clear search highlights easily by pressing Esc
vim.keymap.set("n", "<Esc>", ":noh<CR>", { silent = true })