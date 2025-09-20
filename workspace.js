// Configuration
const API_BASE_URL = window.location.origin;

// State
let currentWorkspace = null;
let currentPage = null;
let workspaces = [];
let pages = [];

// Initialize app
document.addEventListener('DOMContentLoaded', async () => {
  await loadWorkspaces();
  setupEventListeners();
  await loadDashboard();
});

// Setup event listeners
function setupEventListeners() {
  // Navigation
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      const view = item.dataset.view;
      if (view) {
        switchView(view);
        updateNavigation(item);
      }
    });
  });

  // Workspace selector
  document.getElementById('workspace-selector').addEventListener('change', async (e) => {
    const workspaceId = e.target.value;
    if (workspaceId) {
      currentWorkspace = workspaces.find(w => w.id === workspaceId);
      await loadDashboard();
    }
  });

  // Page title auto-save
  document.getElementById('page-title').addEventListener('input', debounce(savePage, 1000));
}

// Utility functions
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

function formatDate(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  });
}

function showLoading(elementId) {
  const element = document.getElementById(elementId);
  if (element) {
    element.innerHTML = `
      <div class="loading">
        <div class="spinner"></div>
        Loading...
      </div>
    `;
  }
}

function showError(elementId, message) {
  const element = document.getElementById(elementId);
  if (element) {
    element.innerHTML = `
      <div class="text-center text-muted">
        <p>❌ ${message}</p>
      </div>
    `;
  }
}

// API functions
async function apiCall(endpoint, options = {}) {
  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      },
      ...options
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return await response.json();
  } catch (error) {
    console.error('API call failed:', error);
    throw error;
  }
}

// Workspace functions
async function loadWorkspaces() {
  try {
    workspaces = await apiCall('/workspaces');
    const selector = document.getElementById('workspace-selector');

    selector.innerHTML = '<option value="">Select Workspace</option>';
    workspaces.forEach(workspace => {
      const option = document.createElement('option');
      option.value = workspace.id;
      option.textContent = `${workspace.icon} ${workspace.name}`;
      selector.appendChild(option);
    });

    // Select first workspace by default
    if (workspaces.length > 0) {
      currentWorkspace = workspaces[0];
      selector.value = currentWorkspace.id;
    }
  } catch (error) {
    console.error('Failed to load workspaces:', error);
  }
}

async function createWorkspace(name, description, icon = '🏠') {
  try {
    const workspace = await apiCall('/workspaces', {
      method: 'POST',
      body: JSON.stringify({ name, description, icon })
    });

    workspaces.push(workspace);
    await loadWorkspaces();
    return workspace;
  } catch (error) {
    console.error('Failed to create workspace:', error);
    throw error;
  }
}

// Dashboard functions
async function loadDashboard() {
  if (!currentWorkspace) return;

  try {
    // Load analytics
    const analytics = await apiCall(`/analytics/${currentWorkspace.id}`);

    document.getElementById('pages-count').textContent = analytics.pages_count;
    document.getElementById('databases-count').textContent = analytics.databases_count;
    document.getElementById('files-count').textContent = analytics.files_count;

    // Load recent activity
    const activityContainer = document.getElementById('recent-activity');
    if (analytics.recent_activity.length === 0) {
      activityContainer.innerHTML = '<p class="text-muted">No recent activity</p>';
    } else {
      activityContainer.innerHTML = analytics.recent_activity.map(activity => `
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border);">
          <div>
            <span>${activity.type === 'page' ? '📄' : '🗃️'}</span>
            <span style="margin-left: 8px;">${activity.name}</span>
          </div>
          <span class="text-muted" style="font-size: 12px;">${formatDate(activity.updated_at)}</span>
        </div>
      `).join('');
    }
  } catch (error) {
    console.error('Failed to load dashboard:', error);
    showError('recent-activity', 'Failed to load dashboard data');
  }
}

// Page functions
async function loadPages() {
  if (!currentWorkspace) return;

  showLoading('pages-list');

  try {
    pages = await apiCall(`/workspaces/${currentWorkspace.id}/pages`);

    const container = document.getElementById('pages-list');
    if (pages.length === 0) {
      container.innerHTML = `
        <div class="text-center text-muted">
          <h3>No pages yet</h3>
          <p>Create your first page to get started</p>
          <button class="btn" onclick="createNewPage()">📄 Create Page</button>
        </div>
      `;
    } else {
      container.innerHTML = `
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px;">
          ${pages.map(page => `
            <div class="dashboard-card" style="cursor: pointer;" onclick="openPage('${page.id}')">
              <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                <span style="font-size: 24px;">${page.icon}</span>
                <div>
                  <h4 style="margin: 0; font-size: 16px;">${page.title}</h4>
                  <p style="margin: 0; font-size: 12px; color: var(--text-muted);">${page.page_type}</p>
                </div>
              </div>
              <p style="font-size: 14px; color: var(--text-secondary); margin: 0;">
                Updated ${formatDate(page.updated_at)}
              </p>
            </div>
          `).join('')}
        </div>
      `;
    }
  } catch (error) {
    console.error('Failed to load pages:', error);
    showError('pages-list', 'Failed to load pages');
  }
}

async function createNewPage() {
  if (!currentWorkspace) {
    alert('Please select a workspace first');
    return;
  }

  try {
    const page = await apiCall('/pages', {
      method: 'POST',
      body: JSON.stringify({
        workspace_id: currentWorkspace.id,
        title: 'Untitled',
        icon: '📄',
        content: [],
        page_type: 'page'
      })
    });

    currentPage = page;
    switchView('page-editor');
    loadPageEditor(page);
  } catch (error) {
    console.error('Failed to create page:', error);
    alert('Failed to create page');
  }
}

async function openPage(pageId) {
  try {
    const page = await apiCall(`/pages/${pageId}`);
    currentPage = page;
    switchView('page-editor');
    loadPageEditor(page);
  } catch (error) {
    console.error('Failed to open page:', error);
    alert('Failed to open page');
  }
}

function loadPageEditor(page) {
  document.getElementById('page-title').value = page.title;
  document.getElementById('page-created').textContent = `Created ${formatDate(page.created_at)}`;
  document.getElementById('page-updated').textContent = `Last edited ${formatDate(page.updated_at)}`;

  const blocksContainer = document.getElementById('page-blocks');
  blocksContainer.innerHTML = '';

  // Load existing blocks or create a default one
  if (page.content && page.content.length > 0) {
    page.content.forEach((block, index) => {
      addBlockToEditor(block, index);
    });
  } else {
    addBlockToEditor({ type: 'paragraph', content: { text: '' } }, 0);
  }
}

function addBlockToEditor(blockData, position) {
  const blocksContainer = document.getElementById('page-blocks');
  const blockElement = document.createElement('div');
  blockElement.className = 'block-container';
  blockElement.innerHTML = `
    <div class="block" data-position="${position}">
      <div class="block-actions">
        <div class="block-handle">⋮⋮</div>
      </div>
      <textarea class="block-content" placeholder="Type something..." data-type="${blockData.type}">${blockData.content.text || ''}</textarea>
    </div>
  `;

  blocksContainer.appendChild(blockElement);

  // Add event listeners
  const textarea = blockElement.querySelector('.block-content');
  textarea.addEventListener('input', debounce(savePage, 1000));
  textarea.addEventListener('keydown', handleBlockKeydown);
}

function addBlock() {
  const blocksContainer = document.getElementById('page-blocks');
  const position = blocksContainer.children.length;
  addBlockToEditor({ type: 'paragraph', content: { text: '' } }, position);

  // Focus the new block
  const newBlock = blocksContainer.lastElementChild.querySelector('.block-content');
  newBlock.focus();
}

function handleBlockKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    addBlock();
  }
}

async function savePage() {
  if (!currentPage) return;

  try {
    const title = document.getElementById('page-title').value;
    const blocks = Array.from(document.querySelectorAll('.block')).map((block, index) => {
      const textarea = block.querySelector('.block-content');
      return {
        type: textarea.dataset.type || 'paragraph',
        content: { text: textarea.value },
        position: index
      };
    });

    const updatedPage = await apiCall(`/pages/${currentPage.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        workspace_id: currentPage.workspace_id,
        title: title,
        icon: currentPage.icon,
        content: blocks,
        page_type: currentPage.page_type,
        properties: currentPage.properties
      })
    });

    currentPage = updatedPage;
    document.getElementById('page-updated').textContent = `Last edited ${formatDate(updatedPage.updated_at)}`;
  } catch (error) {
    console.error('Failed to save page:', error);
  }
}

// Database functions
async function createNewDatabase() {
  if (!currentWorkspace) {
    alert('Please select a workspace first');
    return;
  }

  try {
    const database = await apiCall('/databases', {
      method: 'POST',
      body: JSON.stringify({
        workspace_id: currentWorkspace.id,
        name: 'New Database',
        description: '',
        icon: '🗃️',
        schema: {},
        view_config: {}
      })
    });

    alert('Database created successfully!');
    await loadDashboard();
  } catch (error) {
    console.error('Failed to create database:', error);
    alert('Failed to create database');
  }
}

// File functions
async function uploadFile() {
  if (!currentWorkspace) {
    alert('Please select a workspace first');
    return;
  }

  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;

  input.onchange = async (e) => {
    const files = Array.from(e.target.files);

    for (const file of files) {
      try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${API_BASE_URL}/upload?workspace_id=${currentWorkspace.id}`, {
          method: 'POST',
          body: formData
        });

        if (!response.ok) {
          throw new Error('Upload failed');
        }

        const result = await response.json();
        console.log('File uploaded:', result);
      } catch (error) {
        console.error('Failed to upload file:', error);
        alert(`Failed to upload ${file.name}`);
      }
    }

    alert('Files uploaded successfully!');
    await loadDashboard();
  };

  input.click();
}

// UI functions
function switchView(viewName) {
  // Hide all views
  document.querySelectorAll('.view').forEach(view => {
    view.classList.remove('active');
  });

  // Show selected view
  const targetView = document.getElementById(`${viewName}-view`);
  if (targetView) {
    targetView.classList.add('active');
  }

  // Update title
  const titles = {
    dashboard: 'Dashboard',
    pages: 'Pages',
    databases: 'Databases',
    calendar: 'Calendar',
    search: 'Search',
    templates: 'Templates',
    files: 'Files',
    'ai-assistant': 'AI Assistant',
    'page-editor': currentPage ? currentPage.title : 'Page Editor'
  };

  document.getElementById('main-title').textContent = titles[viewName] || 'Workspace';

  // Load view-specific data
  if (viewName === 'pages') {
    loadPages();
  } else if (viewName === 'dashboard') {
    loadDashboard();
  }
}

function updateNavigation(activeItem) {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.remove('active');
  });
  activeItem.classList.add('active');
}

function toggleTheme() {
  const body = document.body;
  const currentTheme = body.getAttribute('data-theme');
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

  body.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);

  // Update theme toggle button
  const themeButton = document.querySelector('.btn-secondary');
  themeButton.textContent = newTheme === 'dark' ? '☀️' : '🌙';
}

// Modal functions
function showCreateModal() {
  document.getElementById('create-modal').classList.add('active');
}

function hideCreateModal() {
  document.getElementById('create-modal').classList.remove('active');

  // Reset form
  document.getElementById('create-type').value = 'page';
  document.getElementById('create-title').value = '';
  document.getElementById('create-description').value = '';
}

async function handleCreate() {
  const type = document.getElementById('create-type').value;
  const title = document.getElementById('create-title').value.trim();
  const description = document.getElementById('create-description').value.trim();

  if (!title) {
    alert('Please enter a title');
    return;
  }

  try {
    if (type === 'workspace') {
      await createWorkspace(title, description);
      alert('Workspace created successfully!');
    } else if (type === 'page') {
      if (!currentWorkspace) {
        alert('Please select a workspace first');
        return;
      }

      const page = await apiCall('/pages', {
        method: 'POST',
        body: JSON.stringify({
          workspace_id: currentWorkspace.id,
          title: title,
          icon: '📄',
          content: [],
          page_type: 'page'
        })
      });

      currentPage = page;
      switchView('page-editor');
      loadPageEditor(page);
    } else if (type === 'database') {
      if (!currentWorkspace) {
        alert('Please select a workspace first');
        return;
      }

      await apiCall('/databases', {
        method: 'POST',
        body: JSON.stringify({
          workspace_id: currentWorkspace.id,
          name: title,
          description: description,
          icon: '🗃️',
          schema: {},
          view_config: {}
        })
      });

      alert('Database created successfully!');
      await loadDashboard();
    }

    hideCreateModal();
  } catch (error) {
    console.error('Failed to create:', error);
    alert('Failed to create item');
  }
}

function showAIAssistant() {
  switchView('ai-assistant');
  updateNavigation(document.querySelector('[data-view="ai-assistant"]'));
}

// AI Assistant functions
function showContentGenerator() {
  console.log('showContentGenerator called');
  hideAllAITools();
  const element = document.getElementById('content-generator');
  console.log('content-generator element:', element);
  if (element) {
    element.classList.remove('hidden');
  }
}

function showWritingImprover() {
  console.log('showWritingImprover called');
  hideAllAITools();
  const element = document.getElementById('writing-improver');
  if (element) {
    element.classList.remove('hidden');
  }
}

function showTemplateGenerator() {
  console.log('showTemplateGenerator called');
  hideAllAITools();
  const element = document.getElementById('template-generator');
  if (element) {
    element.classList.remove('hidden');
  }
}

function showSummaryGenerator() {
  console.log('showSummaryGenerator called');
  hideAllAITools();
  const element = document.getElementById('summary-generator');
  if (element) {
    element.classList.remove('hidden');
  }
}

function hideAllAITools() {
  console.log('hideAllAITools called');
  document.querySelectorAll('.ai-tool').forEach(tool => {
    tool.classList.add('hidden');
  });
}

async function generateContent() {
  const contentType = document.getElementById('content-type').value;
  const prompt = document.getElementById('content-prompt').value.trim();

  if (!prompt) {
    alert('Please enter a prompt');
    return;
  }

  const resultDiv = document.getElementById('generated-content');
  resultDiv.classList.remove('hidden');
  resultDiv.innerHTML = `
    <div class="ai-loading">
      <div class="spinner"></div>
      Generating content...
    </div>
  `;

  try {
    const response = await apiCall('/ai/generate-content', {
      method: 'POST',
      body: JSON.stringify({
        type: contentType,
        prompt: prompt,
        context: { title: 'Generated Content' }
      })
    });

    resultDiv.innerHTML = `
      <h5>Generated Content:</h5>
      <div style="margin-top: 12px;">${response.content}</div>
      <div style="margin-top: 16px;">
        <button class="btn btn-secondary" onclick="copyToClipboard('${response.content.replace(/'/g, "\\'")}')">Copy</button>
        <button class="btn" onclick="createPageFromAI('${response.content.replace(/'/g, "\\'")}')">Create Page</button>
      </div>
    `;
  } catch (error) {
    console.error('Failed to generate content:', error);
    resultDiv.innerHTML = `
      <div style="color: var(--error);">
        ❌ Failed to generate content. Please check your API key and try again.
      </div>
    `;
  }
}

async function improveWriting() {
  const improvementType = document.getElementById('improvement-type').value;
  const text = document.getElementById('text-to-improve').value.trim();

  if (!text) {
    alert('Please enter text to improve');
    return;
  }

  const resultDiv = document.getElementById('improved-text');
  resultDiv.classList.remove('hidden');
  resultDiv.innerHTML = `
    <div class="ai-loading">
      <div class="spinner"></div>
      Improving writing...
    </div>
  `;

  try {
    const response = await apiCall('/ai/improve-writing', {
      method: 'POST',
      body: JSON.stringify({
        text: text,
        type: improvementType
      })
    });

    resultDiv.innerHTML = `
      <h5>Original:</h5>
      <div style="background: var(--bg-tertiary); padding: 12px; border-radius: 6px; margin: 8px 0;">${response.original_text}</div>
      <h5>Improved:</h5>
      <div style="background: var(--bg-secondary); padding: 12px; border-radius: 6px; margin: 8px 0;">${response.improved_text}</div>
      <div style="margin-top: 16px;">
        <button class="btn btn-secondary" onclick="copyToClipboard('${response.improved_text.replace(/'/g, "\\'")}')">Copy Improved</button>
        <button class="btn" onclick="replaceText('${response.improved_text.replace(/'/g, "\\'")}')">Replace Original</button>
      </div>
    `;
  } catch (error) {
    console.error('Failed to improve writing:', error);
    resultDiv.innerHTML = `
      <div style="color: var(--error);">
        ❌ Failed to improve writing. Please check your API key and try again.
      </div>
    `;
  }
}

async function summarizeContent() {
  const content = document.getElementById('content-to-summarize').value.trim();

  if (!content) {
    alert('Please enter content to summarize');
    return;
  }

  const resultDiv = document.getElementById('content-summary');
  resultDiv.classList.remove('hidden');
  resultDiv.innerHTML = `
    <div class="ai-loading">
      <div class="spinner"></div>
      Generating summary...
    </div>
  `;

  try {
    const response = await apiCall('/ai/generate-content', {
      method: 'POST',
      body: JSON.stringify({
        type: 'summary',
        prompt: `Please provide a concise summary of the following content:\n\n${content}`,
        context: { title: 'Content Summary' }
      })
    });

    resultDiv.innerHTML = `
      <h5>Summary:</h5>
      <div style="margin-top: 12px;">${response.content}</div>
      <div style="margin-top: 16px;">
        <button class="btn btn-secondary" onclick="copyToClipboard('${response.content.replace(/'/g, "\\'")}')">Copy Summary</button>
      </div>
    `;
  } catch (error) {
    console.error('Failed to summarize content:', error);
    resultDiv.innerHTML = `
      <div style="color: var(--error);">
        ❌ Failed to summarize content. Please check your API key and try again.
      </div>
    `;
  }
}

async function applyTemplate(templateType) {
  if (!currentWorkspace) {
    alert('Please select a workspace first');
    return;
  }

  try {
    // Get available templates
    const templates = await apiCall('/templates');
    const template = templates.find(t => t.name.toLowerCase().includes(templateType.replace('-', ' ')));

    if (template) {
      const response = await apiCall(`/templates/${template.id}/apply`, {
        method: 'POST',
        body: JSON.stringify({
          workspace_id: currentWorkspace.id,
          title: `${template.name} - ${new Date().toLocaleDateString()}`
        })
      });

      alert('Template applied successfully!');

      // Open the created page
      if (response.page_id) {
        await openPage(response.page_id);
      }
    } else {
      alert('Template not found');
    }
  } catch (error) {
    console.error('Failed to apply template:', error);
    alert('Failed to apply template');
  }
}

async function createPageFromAI(content) {
  if (!currentWorkspace) {
    alert('Please select a workspace first');
    return;
  }

  try {
    const page = await apiCall('/pages', {
      method: 'POST',
      body: JSON.stringify({
        workspace_id: currentWorkspace.id,
        title: 'AI Generated Content',
        icon: '✨',
        content: [
          {
            type: 'paragraph',
            content: { text: content },
            position: 0
          }
        ],
        page_type: 'page'
      })
    });

    currentPage = page;
    switchView('page-editor');
    loadPageEditor(page);
    alert('Page created successfully!');
  } catch (error) {
    console.error('Failed to create page:', error);
    alert('Failed to create page');
  }
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => {
    alert('Copied to clipboard!');
  }).catch(err => {
    console.error('Failed to copy:', err);
    alert('Failed to copy to clipboard');
  });
}

function replaceText(newText) {
  document.getElementById('text-to-improve').value = newText;
  alert('Text replaced!');
}

// Test function to verify script loading
function testAI() {
  console.log('AI functions are loaded!');
  alert('AI functions are working!');
}

// Initialize theme
document.addEventListener('DOMContentLoaded', () => {
  const savedTheme = localStorage.getItem('theme') || 'light';
  document.body.setAttribute('data-theme', savedTheme);

  const themeButton = document.querySelector('.btn-secondary');
  if (themeButton) {
    themeButton.textContent = savedTheme === 'dark' ? '☀️' : '🌙';
  }

  // Log that the script is loaded
  console.log('Workspace.js loaded successfully');
  console.log('AI Assistant functions available:', {
    showContentGenerator: typeof showContentGenerator,
    showWritingImprover: typeof showWritingImprover,
    showTemplateGenerator: typeof showTemplateGenerator,
    showSummaryGenerator: typeof showSummaryGenerator
  });
});