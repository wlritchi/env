return {
  {
    'nvim-telescope/telescope.nvim',
    tag = '0.1.5',
    cmd = "Telescope",  -- Load only when running :Telescope commands
    keys = {
      -- Load plugin when these keys are pressed
      { '<leader>ff', '<cmd>Telescope find_files<cr>', desc = 'Find files' },
      { '<leader>fg', '<cmd>Telescope live_grep<cr>', desc = 'Live grep' },
      { '<leader>fb', '<cmd>Telescope buffers<cr>', desc = 'Find buffers' },
      { '<leader>fh', '<cmd>Telescope help_tags<cr>', desc = 'Help tags' },
    },
    dependencies = {
      'nvim-lua/plenary.nvim',
      {
        'nvim-telescope/telescope-fzf-native.nvim',
        build = 'make'  -- Compile the C extension for faster fuzzy finding
      }
    },
    config = function()
      require('telescope').setup({
        defaults = {
          file_ignore_patterns = { "node_modules", ".git/" },
        },
      })
      require('telescope').load_extension('fzf')
    end,
  },
}