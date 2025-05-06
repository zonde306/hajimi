<template>
  <div class="daily-stats-section" :class="{ 'animate-in': isVisible }">
    <div class="section-header" @click="toggleCollapse">
      <h3 class="section-title">
        <span class="toggle-icon">{{ isCollapsed ? '▶' : '▼' }}</span>
        每日调用统计
      </h3>
      <div class="section-actions">
        <span class="stat-badge">{{ dailyStats.length }}天</span>
      </div>
    </div>
    
    <div v-show="!isCollapsed" class="daily-stats-content">
      <!-- 统计表格 -->
      <div class="daily-stats-table-container">
        <table class="daily-stats-table">
          <thead>
            <tr>
              <th>日期</th>
              <th>调用次数</th>
              <th>Token使用量</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="stat in dailyStats" :key="stat.date">
              <td>{{ stat.date }}</td>
              <td>
                <span class="stat-value">{{ stat.calls }}</span>
              </td>
              <td>
                <span class="stat-value">{{ stat.tokens }}</span>
              </td>
            </tr>
            <tr v-if="dailyStats.length === 0">
              <td colspan="3" class="empty-data">暂无统计数据</td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td><strong>总计</strong></td>
              <td>
                <span class="stat-value total">{{ totalCalls }}</span>
              </td>
              <td>
                <span class="stat-value total">{{ totalTokens }}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, nextTick, watch } from 'vue'

export default {
  name: 'DailyStatsChart',
  
  setup() {
    const dailyStats = ref([])
    const isVisible = ref(false)
    const isCollapsed = ref(false)
    
    const totalCalls = computed(() => {
      return dailyStats.value.reduce((sum, stat) => sum + (parseInt(stat.calls) || 0), 0)
    })
    
    const totalTokens = computed(() => {
      return dailyStats.value.reduce((sum, stat) => sum + (parseInt(stat.tokens) || 0), 0)
    })
    
    const toggleCollapse = () => {
      isCollapsed.value = !isCollapsed.value
    }
    
    const fetchDailyStats = async () => {
      try {
        // 添加时间戳和随机数避免缓存
        const timestamp = new Date().getTime()
        const random = Math.floor(Math.random() * 10000)
        
        const response = await fetch(`/api/daily-stats?_t=${timestamp}&_r=${random}`, {
          headers: {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache'
          }
        })
        
        if (!response.ok) {
          throw new Error(`HTTP error ${response.status}`)
        }
        
        const data = await response.json()
        
        if (data && data.daily_stats && Array.isArray(data.daily_stats)) {
          // 按日期排序（最新的在前面）
          dailyStats.value = [...data.daily_stats].sort((a, b) => {
            return new Date(b.date) - new Date(a.date)
          })
        } else {
          console.warn('获取的每日统计数据格式不正确')
        }
      } catch (error) {
        console.error('获取每日统计数据失败:', error)
      }
    }
    
    // 组件挂载后自动执行一次获取
    onMounted(() => {
      fetchDailyStats()
      
      // 组件可见性动画
      setTimeout(() => {
        isVisible.value = true
      }, 100)
      
      // 定时刷新数据
      const refreshInterval = setInterval(() => {
        fetchDailyStats()
      }, 60000) // 每分钟刷新一次
      
      // 组件卸载时清除定时器
      return () => clearInterval(refreshInterval)
    })
    
    return {
      dailyStats,
      isVisible,
      isCollapsed,
      totalCalls,
      totalTokens,
      toggleCollapse,
      fetchDailyStats
    }
  }
}
</script>

<style scoped>
.daily-stats-section {
  opacity: 0;
  transform: translateY(10px);
  transition: opacity 0.5s ease, transform 0.5s ease;
  background-color: var(--color-bg-panel);
  border-radius: 8px;
  margin-bottom: 20px;
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.animate-in {
  opacity: 1;
  transform: translateY(0);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 15px;
  cursor: pointer;
  background-color: var(--color-bg-subtle);
  border-bottom: 1px solid var(--color-border);
}

.section-title {
  margin: 0;
  font-size: 1rem;
  color: var(--color-heading);
  display: flex;
  align-items: center;
}

.toggle-icon {
  margin-right: 8px;
  font-size: 0.8rem;
  transition: transform 0.3s ease;
}

.section-actions {
  display: flex;
  align-items: center;
}

.stat-badge {
  background-color: var(--color-primary-50);
  color: var(--color-primary);
  font-size: 0.8rem;
  padding: 2px 8px;
  border-radius: 12px;
}

.daily-stats-content {
  padding: 15px;
}

.daily-stats-table-container {
  margin-bottom: 20px;
  overflow-x: auto;
}

.daily-stats-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

.daily-stats-table th,
.daily-stats-table td {
  padding: 10px;
  border-bottom: 1px solid var(--color-border-light);
}

.daily-stats-table th {
  background-color: var(--color-bg-subtle);
  font-weight: 600;
  color: var(--color-heading);
}

.daily-stats-table tbody tr:hover {
  background-color: var(--color-bg-hover);
}

.daily-stats-table tfoot {
  font-weight: 600;
}

.daily-stats-table tfoot td {
  border-top: 2px solid var(--color-border);
}

.stat-value {
  font-family: var(--font-mono);
  font-size: 0.9rem;
}

.stat-value.total {
  color: var(--color-primary);
  font-weight: 600;
}

.empty-data {
  text-align: center;
  color: var(--color-text-muted);
  padding: 20px;
}

@media (max-width: 640px) {
  .daily-stats-table th,
  .daily-stats-table td {
    padding: 8px 5px;
    font-size: 0.85rem;
  }
}
</style> 