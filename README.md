# mcp-pk-2026 

## Info

#### Project codename: MCP-PK

#### The Team:

Dominik Dróżdż, Magdalena Pabisz, Marcin Walendzik, Szymon Miękina

## Getting started

Open AWS Academy and start lab - wait for the account to be created as indicated by the green dot by the AWS dashboard link on the same toolbar as 'Start Lab' button.

Run `python deploy.py` in the project's root directory - the script will prompt you to copy and paste the AWS credentials, which can be found under the 'Lab Details' tab. From there the script will create the cluster and a node group, apply all the configurations etc.

If you want to automatically setup the load balancers for most services use the `-lb` option. For all available arguments use the `-h` switch.

### deploy.py TODO

- add step that setups up the Ollama with Qwen model,
- add helm install safeguard against installing the Prometheus+Grafana stack the second time,
- add MCP server setup, once it's figured out.
