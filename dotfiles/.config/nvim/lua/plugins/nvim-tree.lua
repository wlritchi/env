return {
  {
    "nvim-tree/nvim-tree.lua",
    lazy = false,
    dependencies = {
      "nvim-tree/nvim-web-devicons",  -- File icons
    },
    config = function()
      require("nvim-tree").setup({
        view = { width = 30 },
        renderer = { group_empty = true },
        filters = { dotfiles = false },
        git = { ignore = false },
      })
      
      -- Key mappings
      vim.keymap.set("n", "<leader>e", "<cmd>NvimTreeToggle<cr>", { desc = "Toggle file explorer" })
      
      -- Sync command
      vim.api.nvim_create_user_command("NvimTreeSyncWithOil", function()
        local oil = require("oil")
        local current_dir = oil.get_current_dir()
        if current_dir then
          vim.cmd("NvimTreeClose")
          vim.cmd("cd " .. vim.fn.fnameescape(current_dir))
          vim.cmd("NvimTreeOpen")
        end
      end, { desc = "Sync nvim-tree with oil's current directory" })
      
      vim.keymap.set("n", "<leader>E", "<cmd>NvimTreeSyncWithOil<cr>", { desc = "Sync tree with oil directory" })
    end,
  },
}