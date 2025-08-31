return {
  {
    "catppuccin/nvim",
    name = "catppuccin",
    priority = 1000,  -- Load this first
    config = function()
      vim.cmd.colorscheme "catppuccin-macchiato"
    end,
  },
}
