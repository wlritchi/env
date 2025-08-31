return {
  {
    "stevearc/oil.nvim",
    lazy = false,
    config = function()
      require("oil").setup({
        columns = { "icon", "permissions", "size", "mtime" },
        view_options = {
          show_hidden = false,
        },
      })
      
      -- Key mappings
      vim.keymap.set("n", "-", "<CMD>Oil<CR>", { desc = "Open parent directory" })
      vim.keymap.set("n", "<leader>o", "<CMD>Oil .<CR>", { desc = "Open current directory" })
      
      -- Auto-open oil and nvim-tree when opening a directory
      vim.api.nvim_create_autocmd("VimEnter", {
        callback = function()
          vim.defer_fn(function()
            local args = vim.fn.argv()
            if #args == 1 then
              local arg = args[1]
              
              -- Handle oil:// prefix if present
              local clean_path = vim.startswith(arg, "oil://") and string.sub(arg, 7) or arg
              
              if vim.fn.isdirectory(clean_path) == 1 then
                vim.cmd("cd " .. vim.fn.fnameescape(clean_path))
                require("oil").open(clean_path)
                
                -- Open tree but keep focus on oil  
                vim.defer_fn(function()
                  local current_win = vim.api.nvim_get_current_win()
                  require("nvim-tree.api").tree.open()
                  vim.api.nvim_set_current_win(current_win)
                end, 200)
              end
            end
          end, 100)
        end,
      })
    end,
    dependencies = { "nvim-tree/nvim-web-devicons" },
  },
}