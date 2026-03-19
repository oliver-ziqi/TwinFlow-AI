# **vibe coding**

## 1、项目描述，这是一个agentic ai for logistics 项目，这个项目一共有三层：

1. 第一层：物理仿真，jaamsim中会把物理传感器数据发送至mqtt topic中

2. 第二层：数字孪生，使用ditto收集状态，使用dashboard显示出来

3. 第三层：agentic ai层，做决策，分析，执行，作用于仿真环境中


## 2、数据源文档：（里面的localhost统一换为：192.168.17.128）

- ### ditto

  - 工厂（ABCD四个）

    - 请求方式： 四个工厂直接改变字母D就可以

      ```shell
      curl -X 'GET' \
        'http://localhost:8080/api/2/things?ids=my.factory%3AfactoryD&fields=thingId%2Cattributes%2Cfeatures' \
        -H 'accept: application/json' \
        -H 'Authorization: Basic ZGl0dG86ZGl0dG8='
        
      [
        {
          "thingId": "my.factory:factoryD",
          "attributes": {
            "name": "factoryD",
            "id": 1,
            "productName": "p4"
          },
          "features": {
            "inventory": {
              "properties": {
                "features": {
                  "inventory": {
                    "properties": {
                      "count": 263
                    }
                  }
                }
              }
            }
          }
        }
      ]
      ```

      

  - 队列 （每个工厂的仓库）共有fc_p2,fc_p23,fc_p3 改变请求参数即可

    - 请求方式：

      ```shell
      curl -X 'GET' \
        'http://localhost:8080/api/2/things?ids=my.queue%3Afc_p2&fields=thingId%2Cattributes%2Cfeatures' \
        -H 'accept: application/json' \
        -H 'Authorization: Basic ZGl0dG86ZGl0dG8='
        
        
      Response Body
      [
        {
          "thingId": "my.queue:fc_p2",
          "attributes": {
            "name": "factoryC",
            "id": 1,
            "productName": "p2"
          },
          "features": {
            "inventory": {
              "properties": {
                "features": {
                  "inventory": {
                    "properties": {
                      "count": 0
                    }
                  }
                }
              }
            }
          }
        }
      ]
      ```

      

  - 小车位置 (共有三个小车，1，2，3，http请求改car后边的id就行)

    - 需要根据属性值做数字孪生：

    - 请求方式：

      ```shell
      curl -X 'GET' \
        'http://localhost:8080/api/2/things?ids=my.logistics%3Acar1&fields=thingId%2Cattributes%2Cfeatures' \
        -H 'accept: application/json' \
        -H 'Authorization: Basic ZGl0dG86ZGl0dG8='
        
      response body:
      [
        {
          "thingId": "my.logistics:car1",
          "attributes": {
            "name": "car1",
            "id": 101,
            "type": "AGV"
          },
          "features": {
            "status": {
              "properties": {
                "features": {
                  "status": {
                    "properties": {
                      "position": {
                        "x": -20.604166499999998,
                        "y": -20.1,
                        "z": 0
                      },
                      "state": "None",
                      "currentObj": "Assemble_p23_2",
                      "count": 0,
                      "storageTime_h": 0
                    }
                  }
                }
              }
            }
          }
        }
      ]
      ```

      

- ### influxdb

  - 请求方式 

    只是给出官方请求规范，具体的bucket 以及查询语句内容可以根据实际需求按照influxDB规范更改

    ```shell
    curl \
      --request POST \
      http://localhost:8086
    /api/v2/query?orgID=my-org
      \
      --header 'Authorization: Token vkl7mO67ZMDwfi7iqUJx-q3ODLxmkq7deYrMbsFxNgoac9Csfh_xwQwHtPHrlmIXd2KOPw4vUiqlG7d3OnRh5g==
    ' \
      --header 'Accept: application/csv' \
      --header 'Content-type: application/vnd.flux' \
      --data 'from(bucket:"queue_data
    ")
            |> range(start: -12h)
            |> filter(fn: (r) => r._measurement == "example-measurement")
            |> aggregateWindow(every: 1h, fn: mean)'
    ```

  - bucket结构

    - 生产队列

      - db结构

      ```markdown
      Bucket: queue_data
          └── Measurement: factory
                  ├── Field:
                  │      └── count
                  │
                  └── Tag:
                         └── thingId
                                ├── my_queue_fc_p2
                                ├── my_queue_fc_p23
                                └── my_queue_fc_p3
      ```

    - 小车位置

      - db结构

      ```markdown
      Bucket: logistics
          └── Measurement: car
                  ├── Field:
                  │      ├── count
                  │      ├── currentObj
                  │      ├── state
                  │      └── storageTime_h
                  │
                  └── Tag:
                         └── thingId
                                ├── my_logistics_car1
                                ├── my_logistics_car2
                                └── my_logistics_car3
      ```

  - 异常事件

    - db结构

      ```markdown
      Bucket: alerts
          └── Measurement: stationary_alert
                  ├── Field:
                  │      └── message
                  │
                  └── Tag:
                         └── alert_type
                                └── motion_stopped
      ```

- ### kafka 

  - 异常消息（根据需求自定义）  
    - 消息生产者：根据alert，有新的alert则发送“异常消息” **topic**
    - 消息消费者：消费alert消息，分析原因
  - 分析结果回传（分析结果自定义）

- ### pgsql 

  - 表结构

    ```markdown
    Database: postgres
    Schema: public
        └── Table: incident_analysis
                ├── id (int8)
                ├── thing_id (text)
                ├── alert_type (text)
                ├── event_time (timestamptz)
                ├── received_at (timestamptz)
                ├── event (jsonb)
                ├── facts (jsonb)
                ├── trace (jsonb)
                └── analysis (text)
                
    Database: postgres
    Schema: public
        └── Table: jaamsim_config
                ├── id (int8)
                ├── config_key (text)
                ├── config_type (text)
                ├── content (jsonb)
                ├── version (text)
                └── created_at (timestamptz)
    ```

    有如下几张配置表：

  | config_key               | config_type      | 说明             |
  | ------------------------ | ---------------- | ---------------- |
  | factory_overview_v1      | jaamsim_overview | 工厂整体结构配置 |
  | car_park_position_config | car_park         | 停车区位置配置   |
  | branch_config            | branch           | 分支路径配置     |
  | assemble_config          | assemble         | 装配节点配置     |

  ```json
  关于overview字段：
  {"legend": {"fx": "factory", "gx": "good", "px": "part"}, "produce": {"fa": ["p1"], "fb": ["p2"], "fc": ["p3"], "fd": ["p4"]}, "assemble": {"fb": [{"in": {"p1": 2, "p2": 1}, "out": "p12"}], "fc": [{"in": {"p1": 1, "p3": 2}, "out": "ga"}, {"in": {"p2": 2, "p3": 2}, "out": "p23"}], "fd": [{"in": {"p4": 3, "p23": 1}, "out": "gb"}]}, "logistics_edges": [{"id": "e1", "to": "fb", "from": "fa", "branch": "branch1", "products": ["p1"], "branch_value": 1}, {"id": "e2", "to": "fc", "from": "fa", "branch": "branch1", "products": ["p1"], "branch_value": 2}, {"id": "e3", "to": "fc", "from": "fb", "branch": "branch2", "products": ["p2"], "branch_value": 1}, {"id": "e4", "to": "fd", "from": "fb", "branch": "branch3", "products": ["p12"], "branch_value": 2}, {"id": "e5", "to": "fd", "from": "fc", "branch": "branch4", "products": ["p23"], "branch_value": 1}]}
  ```

- ### file

  - 项目文件下的branch1、2、3、4 分别控制不同的支路

## 3、实现功能：

1. 异常检测、归因、修改分支文件
2. 用户语义-数据查询
3. 处理用户需求

搭建 langchain  

前端使用 streamlit，集成对话框和 graph



工厂位置：

A : -17.2  3.8  0.0  m

B：4.7  2.1  0.0  m

C：-12.3  -14.4  0.0  m

D：3.5  -14.4  0.0  m



两部分：

- Websocket 推送ditto数据到，state

ditto ws接收消息格式：

```json
[ws-test] recv:
{
  "topic": "my.logistics/car3/things/twin/events/modified",
  "headers": {
    "correlation-id": "463d3095-9d21-4f4c-9832-6d06e44496c4",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "car/c3",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 21631,
    "content-type": "application/json"
  },
  "path": "/features/status/properties",
  "value": {
    "features": {
      "status": {
        "properties": {
          "position": {
            "x": -19.9680555,
            "y": -20.1,
            "z": 0
          },
          "state": "None",
          "currentObj": "p2_9",
          "count": 0,
          "storageTime_h": 0
        }
      }
    }
  },
  "revision": 21631,
  "timestamp": "2026-03-06T09:13:32.923110128Z"
}

```

```
[ws-test] recv:
{
  "topic": "my.factory/factoryD/things/twin/events/modified",
  "headers": {
    "correlation-id": "c9fadbcf-7f34-49c6-947f-e5f69e443043",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "factory/f4",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 21822,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 32
        }
      }
    }
  },
  "revision": 21822,
  "timestamp": "2026-03-06T16:15:21.368875537Z"
}
[ws-test] recv:
{
  "topic": "my.logistics/car3/things/twin/events/modified",
  "headers": {
    "correlation-id": "0e6536a8-87d1-4708-a273-89c5bdaa8da4",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "car/c3",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 22480,
    "content-type": "application/json"
  },
  "path": "/features/status/properties",
  "value": {
    "features": {
      "status": {
        "properties": {
          "position": {
            "x": -19.9680555,
            "y": -20.1,
            "z": 0
          },
          "state": "None",
          "currentObj": "p2_9",
          "count": 0,
          "storageTime_h": 0
        }
      }
    }
  },
  "revision": 22480,
  "timestamp": "2026-03-06T16:15:21.371221710Z"
}
[ws-test] recv:
{
  "topic": "my.factory/factoryD/things/twin/events/modified",
  "headers": {
    "correlation-id": "aa4efb56-52b1-4f85-b054-b35b7e31b8cf",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "factory/f4",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 21823,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 33
        }
      }
    }
  },
  "revision": 21823,
  "timestamp": "2026-03-06T16:15:22.356053712Z"
}
[ws-test] recv:
{
  "topic": "my.factory/factoryC/things/twin/events/modified",
  "headers": {
    "correlation-id": "834a5a14-d783-428b-b577-80a538168232",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "factory/f2",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 21823,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 29
        }
      }
    }
  },
  "revision": 21823,
  "timestamp": "2026-03-06T16:15:22.355251682Z"
}
[ws-test] recv:
{
  "topic": "my.factory/factoryB/things/twin/events/modified",
  "headers": {
    "correlation-id": "2e83637a-56d7-4822-9b83-7135e211988c",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "factory/f2",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 21813,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 29
        }
      }
    }
  },
  "revision": 21813,
  "timestamp": "2026-03-06T16:15:22.385556524Z"
}
[ws-test] recv:
{
  "topic": "my.queue/fc_p2/things/twin/events/modified",
  "headers": {
    "correlation-id": "46fa3c37-6c1e-46ba-8374-0e7741547ce9",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "queue/fc_p2",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 17615,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 0
        }
      }
    }
  },
  "revision": 17615,
  "timestamp": "2026-03-06T16:15:22.390397127Z"
}
[ws-test] recv:
{
  "topic": "my.queue/fc_p3/things/twin/events/modified",
  "headers": {
    "correlation-id": "7bc1eb49-1018-4967-a2e4-358e25d963d1",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "queue/fc_p3",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 17613,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 13
        }
      }
    }
  },
  "revision": 17613,
  "timestamp": "2026-03-06T16:15:22.390471826Z"
}
[ws-test] recv:
{
  "topic": "my.factory/factoryA/things/twin/events/modified",
  "headers": {
    "correlation-id": "2710870a-ece8-4640-8f8a-1afd94901535",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "factory/f1",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 21816,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 30
        }
      }
    }
  },
  "revision": 21816,
  "timestamp": "2026-03-06T16:15:22.391671454Z"
}
[ws-test] recv:
{
  "topic": "my.queue/fc_p23/things/twin/events/modified",
  "headers": {
    "correlation-id": "3e9fb10d-4fb5-497c-947c-64087ec59f32",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "queue/fc_p23",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 17609,
    "content-type": "application/json"
  },
  "path": "/features/inventory/properties",
  "value": {
    "features": {
      "inventory": {
        "properties": {
          "count": 1
        }
      }
    }
  },
  "revision": 17609,
  "timestamp": "2026-03-06T16:15:22.384793111Z"
}
[ws-test] recv:
{
  "topic": "my.logistics/car3/things/twin/events/modified",
  "headers": {
    "correlation-id": "95ee7031-f908-477d-aaf7-f7574aacffcb",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "car/c3",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 22481,
    "content-type": "application/json"
  },
  "path": "/features/status/properties",
  "value": {
    "features": {
      "status": {
        "properties": {
          "position": {
            "x": -19.9680555,
            "y": -20.1,
            "z": 0
          },
          "state": "None",
          "currentObj": "p2_9",
          "count": 0,
          "storageTime_h": 0
        }
      }
    }
  },
  "revision": 22481,
  "timestamp": "2026-03-06T16:15:22.457190071Z"
}
[ws-test] recv:
{
  "topic": "my.logistics/car1/things/twin/events/modified",
  "headers": {
    "correlation-id": "a040f9f3-22a2-4007-af9a-6c940ccd8f07",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "car/c1",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 32968,
    "content-type": "application/json"
  },
  "path": "/features/status/properties",
  "value": {
    "features": {
      "status": {
        "properties": {
          "position": {
            "x": -20.604166499999998,
            "y": -20.1,
            "z": 0
          },
          "state": "None",
          "currentObj": "Assemble_p23_2",
          "count": 0,
          "storageTime_h": 0
        }
      }
    }
  },
  "revision": 32968,
  "timestamp": "2026-03-06T16:15:22.462067150Z"
}
[ws-test] recv:
{
  "topic": "my.logistics/car2/things/twin/events/modified",
  "headers": {
    "correlation-id": "c7a1dd43-2b7d-4a5c-ba6f-32126aaf8fdb",
    "mqtt.qos": "0",
    "mqtt.retain": "false",
    "mqtt.topic": "car/c2",
    "ditto-originator": "nginx:ditto",
    "response-required": false,
    "requested-acks": [],
    "entity-revision": 22470,
    "content-type": "application/json"
  },
  "path": "/features/status/properties",
  "value": {
    "features": {
      "status": {
        "properties": {
          "position": {
            "x": -10.9,
            "y": -13.8,
            "z": 0.01
          },
          "state": "None",
          "currentObj": "p2_20",
          "count": 0,
          "storageTime_h": 0
        }
      }
    }
  },
  "revision": 22470,
  "timestamp": "2026-03-06T16:15:22.467270876Z"

```

```json
msg {"event_id": "79e27929cb577db43783482d29b9721216013c31a057a3c2392b322b15869c6d", "time": "2026-03-07T06:04:30+00:00", "thingId": "my_logistics_car3", "alert_type": "motion_stopped", "message": "Car my_logistics_car3 has been stationary for 1 minute! The car is parked at coordinates {-18.4041665m,3.8m}", "source": "influx-alerts-watcher"}
```

```
{
  "event_id": "demo-stationary-001",
  "time": "2026-03-07T06:04:30+00:00",
  "thingId": "my_logistics_car3",
  "alert_type": "motion_stopped",
  "message": "Car my_logistics_car3 has been stationary for 1 minute! The car is parked at coordinates {-18.4041665m,3.8m}",
  "source": "influx-alerts-watcher"
}

```



### InfluxDB Task

```python
import "http"
import "json"
option task = { 
  name: "detect_stationary_car_1m",
  every: 30s,
  offset: 10s
}

carData =
    from(bucket: "logistics")
        |> range(start: -1m)
        |> filter(fn: (r) => r._measurement == "car")
        |> filter(fn: (r) => r._field == "x" or r._field == "y")
        |> pivot(rowKey: ["_time", "thingId"], columnKey: ["_field"], valueColumn: "_value")
        |> filter(fn: (r) => exists r.x and exists r.y)

stationaryCars =
    carData
        |> group(columns: ["thingId"])
        |> aggregateWindow(
            every: 1m,
            fn: (column, tables=<-) => {
                xSpread =
                    tables
                        |> map(fn: (r) => ({r with _value: r.x}))
                        |> spread(column: "_value")
                        |> findRecord(fn: (key) => true, idx: 0)

                ySpread =
                    tables
                        |> map(fn: (r) => ({r with _value: r.y}))
                        |> spread(column: "_value")
                        |> findRecord(fn: (key) => true, idx: 0)

                return
                    tables
                        |> limit(n: 1)
                        |> map(
                            fn: (r) =>
                                ({r with x_spread:
                                        if exists xSpread._value then xSpread._value else 0.0,
                                    y_spread: if exists ySpread._value then ySpread._value else 0.0,
                                }),
                        )
            },
        )
        |> filter(fn: (r) => r.x_spread <= 0.0 and r.y_spread <= 0.0)
        |> keep(
            columns: [
                "thingId",
                "x",
                "y",
                "x_spread",
                "y_spread",
            ],
        )

alerts =
    stationaryCars
        |> map(
            fn: (r) =>
                ({
                    _measurement: "stationary_alert",
                    _field: "message",
                    _value:
                        "Car " + r.thingId
                            +
                            " has been stationary for 1 minute! The car is parked at coordinates {"
                            +
                            string(v: r.x) + "m," + string(v: r.y) + "m}",
                    thingId: r.thingId,
                    alert_type: "motion_stopped",
                    _time: now(),
                }),
        )

alerts |> to(bucket: "alerts", org: "my-org")
```

problem:

1. 判断异常逻辑有问题，无论如何都应该改小车所在的分支
2. 数字孪生界面没法用
3. 集成面板
4. curl -fsSL https://openclaw.ai/install.cmd -o install.cmd && install.cmd --tag beta && del install.cmd

